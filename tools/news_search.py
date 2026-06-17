"""
新闻搜索工具 — 在已获取的新闻中进行关键词搜索和过滤
"""
from typing import Optional

from utils.db import NewsDatabase

# 工具定义 — 用于 DeepSeek function calling
SEARCH_NEWS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_news",
        "description": (
            "在已获取的新闻数据库中按关键词搜索。"
            "支持多关键词 OR 搜索、按分类过滤、按语言过滤。"
            "用于查找特定主题的新闻，或在分析前缩小范围。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "搜索关键词列表（OR 逻辑），例如 ['AI', '大模型', 'GPT']",
                },
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选：限制搜索的分类。留空则搜索全部分类。",
                },
                "language": {
                    "type": "string",
                    "enum": ["zh", "en", "all"],
                    "description": "语言过滤：zh=中文, en=英文, all=全部。默认 all。",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回结果最大数量，默认 30",
                },
            },
            "required": ["keywords"],
        },
    },
}


async def search_news(
    keywords: list[str],
    categories: Optional[list[str]] = None,
    language: str = "all",
    limit: int = 30,
    db: Optional[NewsDatabase] = None,
) -> dict:
    """
    在新闻数据库中按关键词搜索

    Returns:
        {
            "total_found": int,
            "keywords": [...],
            "articles": [...]
        }
    """
    if not db:
        return {"total_found": 0, "keywords": keywords, "articles": [], "error": "数据库未初始化"}

    articles = await db.search_articles(keywords, limit=limit)

    # 按分类过滤（如果数据库查询后还需要细分）
    if categories and "all" not in categories:
        articles = [a for a in articles if a.get("category") in categories]

    # 按语言过滤
    if language != "all":
        articles = [a for a in articles if a.get("language") == language]

    # 格式化输出
    formatted = []
    for a in articles:
        formatted.append({
            "title": a["title"],
            "url": a.get("url", ""),
            "source": a.get("source_name", ""),
            "category": a.get("category", ""),
            "language": a.get("language", "en"),
            "summary": a.get("summary", "")[:300] if a.get("summary") else "",
            "published": a.get("published_at", ""),
        })

    return {
        "total_found": len(formatted),
        "keywords": keywords,
        "articles": formatted,
    }
