#!/usr/bin/env python3
"""
每日新闻智能摘要 Agent — 入口

使用方法:
    # 图形界面（默认）
    python main.py

    # 交互模式
    python main.py --cli

    # 单次模式（指定提示词）
    python main.py --prompt "总结今天AI领域最重要的5条新闻"

    # 预览模式（不真实发送邮件）
    python main.py --dry-run --prompt "今日科技新闻摘要"

    # 单次模式 + 直接发送
    python main.py --send --prompt "总结今日财经要闻" --email your@email.com
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    # 加载 .env 文件
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv 未安装时跳过

from agent import NewsAgent
from utils.db import NewsDatabase

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


async def async_main():
    parser = argparse.ArgumentParser(
        description="📰 每日新闻智能摘要 Agent — 基于 DeepSeek API + 60+ 全球 RSS 源",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                                      # 图形界面
  python main.py --cli                                # 终端交互模式
  python main.py --prompt "今天AI领域有什么大事"        # 单次运行
  python main.py --dry-run --prompt "科技新闻摘要"      # 预览模式
  python main.py --send --prompt "财经要闻" --email me@qq.com  # 发送邮件
        """,
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="使用终端交互模式（无参数启动默认进入图形界面）",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="启动图形界面",
    )
    parser.add_argument(
        "--prompt", "-p",
        type=str,
        help="单次模式的提示词（不提供则进入交互模式）",
    )
    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="预览模式：不真实发送邮件，HTML 保存到 data/email_previews/",
    )
    parser.add_argument(
        "--send", "-s",
        action="store_true",
        help="允许真实发送邮件（需要配置 SMTP 环境变量）",
    )
    parser.add_argument(
        "--email", "-e",
        type=str,
        help="收件人邮箱（覆盖默认值）",
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=None,
        help="DeepSeek 模型名称（默认 deepseek-chat）",
    )

    args = parser.parse_args()

    if args.gui:
        from gui import launch_gui
        launch_gui()
        return

    # 检查 API Key
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("=" * 60)
        print("  ❌ 未设置 DEEPSEEK_API_KEY")
        print("=" * 60)
        print("\n请通过以下任一方式设置:")
        print("  1. 创建 .env 文件（参考 .env.example）")
        print("     cp .env.example .env")
        print("     # 编辑 .env 填入你的 API key")
        print()
        print("  2. 设置环境变量")
        print('     export DEEPSEEK_API_KEY="sk-your-key-here"')
        print()
        print("  DeepSeek API 申请: https://platform.deepseek.com/")
        sys.exit(1)

    # 初始化数据库
    db = NewsDatabase("data/news.db")
    await db.init()
    await db.cleanup_old_articles(7)

    # 创建 Agent
    agent = NewsAgent(db=db, model=args.model)

    # 交互模式
    if not args.prompt:
        await agent.run_interactive()
        return

    # 单次模式
    user_prompt = args.prompt
    if args.email:
        user_prompt += f"\n\n请将摘要发送到邮箱: {args.email}"

    is_dry = not args.send
    if is_dry:
        print("📧 预览模式（邮件将保存为 HTML 文件而非真实发送）\n")

    print(f"🚀 运行中... 提示词: {args.prompt[:100]}\n")

    try:
        result = await agent.run(user_prompt, dry_run=is_dry)
        print("\n" + "=" * 70)
        print(result)
        print("=" * 70)
    except ValueError as e:
        print(f"\n❌ 配置错误: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 运行错误: {e}")
        logger.exception("Agent run failed")
        sys.exit(1)


def main():
    if len(sys.argv) == 1:
        from gui import launch_gui
        launch_gui()
        return
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
