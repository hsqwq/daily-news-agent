"""
Agent 编排器 — ReAct 风格的 Function Calling 循环

核心逻辑：
1. 接收用户输入
2. 调用 DeepSeek API（带工具定义）
3. 如果 LLM 返回 tool_calls → 执行工具 → 结果注入上下文 → 继续循环
4. 如果 LLM 返回文本 → 任务完成，输出结果
5. 最大迭代保护，防止无限循环
"""
import asyncio
import json
import logging
import os
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import yaml
from openai import AsyncOpenAI

from agent.system_prompt import SYSTEM_PROMPT
from tools import (
    ALL_TOOL_SCHEMAS,
    TOOL_MAP,
    get_available_categories,
)
from utils.db import NewsDatabase

logger = logging.getLogger(__name__)


class NewsAgent:
    """每日新闻智能摘要 Agent"""

    def __init__(self, db: Optional[NewsDatabase] = None, model: Optional[str] = None):
        self.db = db

        # 加载配置
        self.settings = self._load_settings()

        # 初始化 DeepSeek 客户端
        api_key = self._resolve_env(self.settings["llm"]["api_key"])
        base_url = self._resolve_env(self.settings["llm"]["base_url"])

        if not api_key or api_key.startswith("${"):
            raise ValueError(
                "DeepSeek API Key 未设置。请在 .env 文件中设置 DEEPSEEK_API_KEY，"
                "或设置环境变量 DEEPSEEK_API_KEY=sk-xxx"
            )

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
        )

        self.model = model or os.environ.get("DEEPSEEK_MODEL") or self.settings["llm"].get("model", "deepseek-chat")
        self.temperature = float(os.environ.get("LLM_TEMPERATURE") or self.settings["llm"].get("temperature", 0.3))
        self.max_tokens = int(os.environ.get("LLM_MAX_TOKENS") or self.settings["llm"].get("max_tokens", 4096))
        self.max_tool_calls = int(os.environ.get("MAX_TOOL_CALLS") or self.settings["agent"].get("max_tool_calls", 15))

        # 会话状态
        self.messages: list[dict] = []
        self.tool_call_count = 0
        self.articles_collected = 0

    def _load_settings(self) -> dict:
        config_path = Path(__file__).parent.parent / "config" / "settings.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            raw = f.read()
        config = yaml.safe_load(raw)
        return config

    def _resolve_env(self, value: str) -> str:
        """解析 ${VAR:-default} 格式的占位符"""
        def replacer(match):
            expr = match.group(1)
            if ":-" in expr:
                var, default = expr.split(":-", 1)
                return os.environ.get(var.strip(), default.strip())
            return os.environ.get(expr.strip(), "")

        return re.sub(r"\$\{([^}]+)\}", replacer, value) if isinstance(value, str) else value

    def _default_recipient(self) -> str:
        """从配置中解析默认收件人，供模型漏填或填占位邮箱时兜底。"""
        email_config = self.settings.get("email", {})
        return self._resolve_env(email_config.get("default_recipient", ""))

    async def _execute_tool(self, tool_name: str, arguments: dict) -> dict:
        """执行工具调用。"""
        logger.info(f"执行工具: {tool_name}({json.dumps(arguments, ensure_ascii=False)[:200]})")

        func = TOOL_MAP.get(tool_name)
        if not func:
            return {"error": f"未知工具: {tool_name}"}

        try:
            # 注入 db 实例（如果工具需要）
            sig_kwargs = dict(arguments)
            if tool_name == "send_email":
                if not sig_kwargs.get("body_html", "").strip():
                    return {
                        "success": False,
                        "message": (
                            "send_email 缺少 body_html，邮件未发送。请立即重新调用 send_email，"
                            "并提供完整 HTML 正文，不要只传 recipient 和 subject。"
                        ),
                    }
                recipient = sig_kwargs.get("recipient", "")
                if not recipient or recipient.endswith("@example.com"):
                    default_recipient = self._default_recipient()
                    if not default_recipient:
                        return {
                            "success": False,
                            "message": "缺少收件人，且 DEFAULT_RECIPIENT 未配置，邮件未发送。",
                        }
                    sig_kwargs["recipient"] = default_recipient
            if "db" in func.__code__.co_varnames[: func.__code__.co_argcount]:
                sig_kwargs["db"] = self.db

            result = await func(**sig_kwargs)
            return result
        except TypeError as e:
            # 如果参数不匹配，尝试不带 db 调用
            logger.warning(f"工具调用参数不匹配 (尝试降级): {e}")
            try:
                result = await func(**arguments)
                return result
            except Exception as e2:
                return {"error": f"工具执行失败: {str(e2)}"}
        except Exception as e:
            logger.error(f"工具执行失败: {e}")
            return {"error": f"工具执行失败: {str(e)}"}

    def _format_tool_result_for_llm(self, result: dict, max_chars: int = 4000) -> str:
        """
        格式化工具结果为 LLM 可读的文本。
        对大量文章列表做截断，保留关键信息。
        """
        if isinstance(result, dict) and "articles" in result:
            articles = result["articles"]
            total_fetched = result.get("total_fetched", len(articles))

            # 构建精简的结果
            lines = [
                f"📊 获取结果: 共 {total_fetched} 篇文章",
                f"成功源: {result.get('feeds_success', '?')} | 失败源: {result.get('feeds_failed', '?')}",
            ]

            # 按分类统计
            by_cat = result.get("by_category", {})
            if by_cat:
                lines.append(f"分类分布: {json.dumps(by_cat, ensure_ascii=False)}")

            # 错误信息
            errors = result.get("errors", [])
            if errors:
                lines.append(f"⚠️ 错误 ({len(errors)}): {'; '.join(errors[:3])}")

            # 文章列表
            lines.append(f"\n📋 文章列表 ({len(articles)} 篇，以下为前 30 篇):")
            for i, a in enumerate(articles[:30]):
                title = a.get("title", "无标题")[:80]
                source = a.get("source_name", "?")
                cat = a.get("category", "?")
                pub = (a.get("published_at") or "")[:16]
                url = a.get("url", "")[:60]
                lines.append(f"  [{i+1}] [{cat}] {title} | {source} | {pub}")
                lines.append(f"       {url}")

            if len(articles) > 30:
                lines.append(f"  ... 还有 {len(articles) - 30} 篇文章未显示")

            result_text = "\n".join(lines)

        elif isinstance(result, dict) and "content" in result:
            # content fetcher 结果特殊处理
            result_text = json.dumps(result, ensure_ascii=False, indent=2)

        else:
            result_text = json.dumps(result, ensure_ascii=False, indent=2)

        # 截断过长的结果
        if len(result_text) > max_chars:
            result_text = result_text[:max_chars] + f"\n... (截断，总长度 {len(result_text)} 字符)"

        return result_text

    async def run(self, user_prompt: str, dry_run: bool = False) -> str:
        """
        运行 Agent。

        Args:
            user_prompt: 用户输入的提示词
            dry_run: 已废弃，保留参数仅用于兼容旧调用

        Returns:
            Agent 的最终响应文本
        """
        self.tool_call_count = 0

        # 构建消息列表
        today = datetime.now(timezone.utc).strftime("%Y年%m月%d日")

        # 获取可用分类信息
        categories = get_available_categories()
        cat_info = "\n".join([
            f"  - **{cat}** ({info['label']}): {info['description']} ({info['feed_count']} 个源)"
            for cat, info in categories.items()
        ])
        default_recipient = self._default_recipient()

        self.messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT + f"\n\n## 当前日期\n今天是 {today} (UTC)。用户只关心最近 24-48 小时内的新闻。"
                    f"\n\n## 默认邮件收件人\n如果用户没有在提示词中指定邮箱，send_email 的 recipient 必须使用：{default_recipient}。"
                    "不要向用户追问邮箱地址；除非工具返回发送失败，否则应直接调用 send_email 完成发送。"
                    f"\n\n## 可用 RSS 分类\n{cat_info}",
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ]

        # ReAct 循环
        max_iterations = self.max_tool_calls
        for iteration in range(max_iterations):
            logger.info(f"--- Agent 迭代 {iteration + 1}/{max_iterations} ---")

            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    tools=ALL_TOOL_SCHEMAS,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    # DeepSeek 的 tool_choice 支持
                    # tool_choice="auto",
                )
            except Exception as e:
                logger.error(f"DeepSeek API 调用失败: {e}")
                return f"❌ Agent 运行失败：DeepSeek API 调用出错 — {str(e)}"

            message = response.choices[0].message

            # 追加 assistant 消息
            assistant_msg = {"role": "assistant", "content": message.content or ""}
            if message.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ]
            self.messages.append(assistant_msg)

            # 如果没有工具调用，说明 Agent 完成了
            if not message.tool_calls:
                final_text = message.content or ""
                logger.info(f"Agent 完成: 共 {iteration + 1} 轮迭代, {self.tool_call_count} 次工具调用")
                return final_text

            # 执行工具调用
            for tool_call in message.tool_calls:
                self.tool_call_count += 1
                tool_name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}

                # 执行
                result = await self._execute_tool(tool_name, arguments)

                # 统计
                if tool_name == "fetch_rss_feeds":
                    self.articles_collected = result.get("total_fetched", 0)

                # 格式化结果
                result_text = self._format_tool_result_for_llm(result)

                # 追加工具结果消息
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_text,
                })

            # 防止无限循环
            if iteration == max_iterations - 1:
                logger.warning(f"达到最大迭代次数 {max_iterations}，强制要求总结")
                self.messages.append({
                    "role": "user",
                    "content": "你已经达到了最大工具调用次数。请基于已获取的信息，立即生成最终新闻摘要，"
                    "包括 HTML 邮件正文，并调用 send_email 发送。不要继续调用其他工具。",
                })
                # 最后再给一次机会
                try:
                    response = await self.client.chat.completions.create(
                        model=self.model,
                        messages=self.messages,
                        tools=ALL_TOOL_SCHEMAS,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                    )
                    final = response.choices[0].message.content or ""
                    return final
                except Exception:
                    return "Agent 达到最大迭代次数但无法完成。"

        return "Agent 未能在限定轮次内完成任务。"

    async def run_interactive(self):
        """交互模式：持续接收用户输入"""
        print("\n" + "=" * 70)
        print("  📰 每日新闻智能摘要 Agent")
        print("  基于 DeepSeek API · 60+ 全球 RSS 源 · 4 核心工具")
        print("=" * 70)
        print("\n输入你的需求（例如「总结今天最重要的 10 条 AI 新闻」）")
        print("输入 'categories' 查看可用分类 · 'stats' 查看统计 · 'quit' 退出")
        print("-" * 70)

        while True:
            try:
                user_input = input("\n🧑 你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 再见！")
                break

            if not user_input:
                continue

            if user_input.lower() in ("quit", "exit", "q"):
                print("👋 再见！")
                break

            if user_input.lower() == "categories":
                cats = get_available_categories()
                print("\n📂 可用新闻分类:")
                for cat, info in cats.items():
                    print(f"  {info['label']} ({cat}): {info['description']} [{info['feed_count']}源]")
                continue

            if user_input.lower() == "stats" and self.db:
                stats = await self.db.get_stats()
                print(f"\n📊 数据库统计: 共 {stats['total_articles']} 篇文章")
                for cat, count in stats.get("by_category", {}).items():
                    print(f"  {cat}: {count} 篇")
                continue

            print("\n🤖 Agent 正在工作...\n")

            try:
                result = await self.run(user_input, dry_run=False)
                print("\n" + "-" * 50)
                print(result)
                print("-" * 50)
            except ValueError as e:
                print(f"\n❌ 配置错误: {e}")
                print("请先设置 DEEPSEEK_API_KEY 环境变量。参考 .env.example")
            except Exception as e:
                print(f"\n❌ 运行错误: {e}")
                logger.exception("Agent run failed")
