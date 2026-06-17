"""
Agent 工具集 — 4 个核心工具
"""
from tools.rss_fetcher import fetch_rss_feeds, FETCH_RSS_SCHEMA, get_available_categories
from tools.content_fetcher import fetch_article_content, FETCH_CONTENT_SCHEMA
from tools.news_search import search_news, SEARCH_NEWS_SCHEMA
from tools.email_sender import send_email, SEND_EMAIL_SCHEMA

# 所有工具的函数调用 schema 列表（传给 DeepSeek API）
ALL_TOOL_SCHEMAS = [
    FETCH_RSS_SCHEMA,
    FETCH_CONTENT_SCHEMA,
    SEARCH_NEWS_SCHEMA,
    SEND_EMAIL_SCHEMA,
]

# 工具名称到函数的映射
TOOL_MAP = {
    "fetch_rss_feeds": fetch_rss_feeds,
    "fetch_article_content": fetch_article_content,
    "search_news": search_news,
    "send_email": send_email,
}

__all__ = [
    "fetch_rss_feeds",
    "fetch_article_content",
    "search_news",
    "send_email",
    "get_available_categories",
    "FETCH_RSS_SCHEMA",
    "FETCH_CONTENT_SCHEMA",
    "SEARCH_NEWS_SCHEMA",
    "SEND_EMAIL_SCHEMA",
    "ALL_TOOL_SCHEMAS",
    "TOOL_MAP",
]
