"""
RSS 获取工具 — 从多个 RSS 源批量获取新闻
支持分类批量获取、并发请求、自动去重入库
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import feedparser
import httpx
import yaml
from pathlib import Path

from utils.db import NewsDatabase
from utils.text_utils import clean_html, truncate_text

logger = logging.getLogger(__name__)

# 加载 RSS 源配置
def _load_feeds_config() -> dict:
    config_path = Path(__file__).parent.parent / "config" / "feeds.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

FEEDS_CONFIG = _load_feeds_config()

# 工具定义 — 用于 DeepSeek function calling
FETCH_RSS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fetch_rss_feeds",
        "description": (
            "从指定的 RSS 新闻源分类批量获取最新新闻。"
            "支持按分类获取（ai/tech/news/finance/science/programming/all），"
            "也可指定具体 RSS 源 URL。"
            "返回新闻列表，每项包含标题、链接、来源、发布时间、摘要。"
            "获取到的新闻会自动存入数据库并去重。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["ai", "tech", "news", "finance", "science", "programming", "all"],
                    },
                    "description": "要获取的新闻分类列表。可选 ai/tech/news/finance/science/programming/all。默认 all。",
                },
                "specific_feeds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选: 指定具体 RSS 源名称列表，用于精确获取某个源的新闻。与 categories 二选一或组合使用。",
                },
                "max_per_feed": {
                    "type": "integer",
                    "description": "每个 RSS 源最多获取的文章数，默认 10",
                },
                "since_hours": {
                    "type": "integer",
                    "description": "只获取最近 N 小时发布的新闻，默认 48",
                },
            },
            "required": ["categories"],
        },
    },
}


def get_available_categories() -> dict:
    """返回可用的 RSS 分类及源数量"""
    return {
        cat: {
            "label": info["label"],
            "description": info["description"],
            "feed_count": len(info["feeds"]),
        }
        for cat, info in FEEDS_CONFIG["categories"].items()
    }


def get_feeds_for_categories(categories: list[str]) -> list[dict]:
    """根据分类列表获取所有对应的 RSS 源"""
    if "all" in categories:
        categories = list(FEEDS_CONFIG["categories"].keys())

    feeds = []
    seen = set()
    for cat in categories:
        if cat not in FEEDS_CONFIG["categories"]:
            continue
        for feed in FEEDS_CONFIG["categories"][cat]["feeds"]:
            if feed["url"] not in seen:
                feed_with_cat = {**feed, "category": cat}
                feeds.append(feed_with_cat)
                seen.add(feed["url"])
    return feeds


def get_feeds_by_name(names: list[str]) -> list[dict]:
    """根据源名称获取 RSS 源"""
    feeds = []
    for cat, info in FEEDS_CONFIG["categories"].items():
        for feed in info["feeds"]:
            if feed["name"] in names:
                feeds.append({**feed, "category": cat})
    return feeds


async def fetch_single_feed(
    client: httpx.AsyncClient,
    feed_info: dict,
    max_entries: int = 10,
    timeout: int = 15,
) -> tuple[str, list[dict], Optional[str]]:
    """获取单个 RSS 源的内容，返回 (feed_name, entries, error)"""
    name = feed_info["name"]
    url = feed_info["url"]
    category = feed_info["category"]
    language = feed_info.get("language", "en")

    try:
        response = await client.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()

        feed = feedparser.parse(response.text)
        if feed.bozo and not feed.entries:
            return name, [], f"RSS 解析错误: {feed.bozo_exception}"

        entries = []
        for entry in feed.entries[:max_entries]:
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
                except (ValueError, TypeError):
                    pass
            if not published and hasattr(entry, "updated_parsed") and entry.updated_parsed:
                try:
                    published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc).isoformat()
                except (ValueError, TypeError):
                    pass

            summary = entry.get("summary") or entry.get("description") or ""
            summary = clean_html(summary)

            entries.append({
                "title": entry.get("title", "无标题").strip(),
                "url": entry.get("link", "").strip(),
                "source_name": name,
                "category": category,
                "language": language,
                "summary": truncate_text(summary, 500),
                "published_at": published,
            })

        return name, entries, None

    except httpx.TimeoutException:
        return name, [], f"请求超时 ({timeout}s)"
    except httpx.HTTPStatusError as e:
        return name, [], f"HTTP 错误: {e.response.status_code}"
    except Exception as e:
        return name, [], f"未知错误: {str(e)}"


async def fetch_rss_feeds(
    categories: Optional[list[str]] = None,
    specific_feeds: Optional[list[str]] = None,
    max_per_feed: int = 10,
    since_hours: int = 48,
    db: Optional[NewsDatabase] = None,
) -> dict:
    """
    批量获取 RSS 新闻。

    Args:
        categories: 分类列表，默认 ["all"]
        specific_feeds: 指定源名称列表
        max_per_feed: 每个源最多条目数
        since_hours: 时间范围
        db: 数据库实例（用于去重入库）

    Returns:
        {
            "total_fetched": int,       # 获取到的总条目数
            "new_articles": int,        # 新入库的条目数
            "by_category": {...},       # 按分类统计
            "feeds_success": int,       # 成功的源数
            "feeds_failed": int,        # 失败的源数
            "errors": [...],            # 错误列表
            "articles": [...]           # 全部文章列表
        }
    """
    categories = categories or ["all"]
    feeds = get_feeds_for_categories(categories)
    if specific_feeds:
        feeds.extend(get_feeds_by_name(specific_feeds))

    if not feeds:
        return {"total_fetched": 0, "new_articles": 0, "by_category": {}, "feeds_success": 0, "feeds_failed": 0, "errors": ["未找到匹配的 RSS 源"], "articles": []}

    # 加载设置中的并发数
    settings_path = Path(__file__).parent.parent / "config" / "settings.yaml"
    with open(settings_path, "r", encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    max_concurrent = settings.get("rss", {}).get("max_concurrent", 10)
    timeout = settings.get("rss", {}).get("request_timeout", 15)
    user_agent = settings.get("rss", {}).get("user_agent", "DailyNewsAgent/1.0")

    headers = {"User-Agent": user_agent}
    semaphore = asyncio.Semaphore(max_concurrent)

    async def fetch_with_semaphore(client, feed_info):
        async with semaphore:
            return await fetch_single_feed(client, feed_info, max_per_feed, timeout)

    async with httpx.AsyncClient(headers=headers, http2=True) as client:
        tasks = [fetch_with_semaphore(client, f) for f in feeds]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_articles = []
    errors = []
    feeds_success = 0
    feeds_failed = 0

    for result in results:
        if isinstance(result, Exception):
            feeds_failed += 1
            errors.append(f"内部错误: {str(result)}")
            continue

        name, entries, error = result
        if error:
            feeds_failed += 1
            errors.append(f"[{name}] {error}")
            if db:
                await db.update_feed_state(
                    next((f["url"] for f in feeds if f["name"] == name), ""),
                    error=error,
                )
        else:
            feeds_success += 1
            all_articles.extend(entries)
            if db:
                await db.update_feed_state(
                    next((f["url"] for f in feeds if f["name"] == name), ""),
                )

    # 去重入库
    new_articles = 0
    if db and all_articles:
        new_articles = await db.batch_insert_articles(all_articles)

    # 按分类统计
    by_category: dict[str, int] = {}
    for a in all_articles:
        cat = a["category"]
        by_category[cat] = by_category.get(cat, 0) + 1

    return {
        "total_fetched": len(all_articles),
        "new_articles": new_articles,
        "by_category": by_category,
        "feeds_success": feeds_success,
        "feeds_failed": feeds_failed,
        "errors": errors[:10],  # 只返回前 10 条错误
        "articles": all_articles,
    }
