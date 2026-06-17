"""
Agent 数据模型 — Pydantic 定义
"""
from pydantic import BaseModel, Field
from typing import Optional


class ArticleSummary(BaseModel):
    """单篇新闻条目"""
    title: str
    url: str
    source_name: str = ""
    category: str = ""
    language: str = "en"
    summary: str = ""
    published_at: Optional[str] = None


class RSSFetchResult(BaseModel):
    """RSS 获取结果"""
    total_fetched: int
    new_articles: int
    by_category: dict[str, int] = {}
    feeds_success: int = 0
    feeds_failed: int = 0
    errors: list[str] = []
    articles: list[ArticleSummary] = []


class ContentFetchResult(BaseModel):
    """网页正文抓取结果"""
    url: str
    title: str = ""
    content: str = ""
    text_length: int = 0
    error: Optional[str] = None


class SearchResult(BaseModel):
    """新闻搜索结果"""
    title: str
    url: str
    source: str = ""
    category: str = ""
    language: str = "en"
    summary: str = ""
    published: str = ""


class EmailResult(BaseModel):
    """邮件发送结果"""
    success: bool
    message: str
    preview_path: Optional[str] = None


class AgentState(BaseModel):
    """Agent 运行状态"""
    messages: list[dict] = []
    tool_calls_count: int = 0
    articles_collected: int = 0
    categories_covered: list[str] = []
    phase: str = "init"  # init | fetching | analyzing | summarizing | sending | done
