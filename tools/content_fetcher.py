"""
网页正文抓取工具 — 通过 readability 提取网页正文
用于对重点新闻进行深度阅读和分析
"""
import asyncio
import logging
from typing import Optional

import httpx
from readability import Document
from bs4 import BeautifulSoup

from utils.db import NewsDatabase
from utils.text_utils import truncate_text, clean_html

logger = logging.getLogger(__name__)

# 工具定义 — 用于 DeepSeek function calling
FETCH_CONTENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fetch_article_content",
        "description": (
            "抓取指定 URL 的网页正文内容。"
            "使用 readability 算法提取网页核心内容，去除广告、导航等干扰。"
            "用于对重点新闻进行深度阅读和详细分析。"
            "支持批量获取，返回每篇文章的标题、正文文本、长度等信息。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要抓取正文的 URL 列表，最多 10 个",
                    "maxItems": 10,
                },
                "max_length": {
                    "type": "integer",
                    "description": "每篇文章正文的最大字符数，默认 3000",
                },
            },
            "required": ["urls"],
        },
    },
}


async def fetch_single_content(
    client: httpx.AsyncClient,
    url: str,
    max_length: int = 3000,
    timeout: int = 20,
) -> dict:
    """
    抓取单个 URL 的正文内容

    Returns:
        {"url": str, "title": str, "content": str, "text_length": int, "error": str|None}
    """
    try:
        response = await client.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()

        html = response.text
        doc = Document(html)

        # 提取标题
        title = doc.title() or ""

        # 提取正文
        content_html = doc.summary()
        soup = BeautifulSoup(content_html, "lxml" if "lxml" in BeautifulSoup.__dict__ else "html.parser")
        content_text = soup.get_text(separator="\n", strip=True)
        content_text = clean_html(content_text)

        # 如果 readability 提取内容太少，尝试直接从 body 提取
        if len(content_text) < 100:
            body_soup = BeautifulSoup(html, "lxml" if "lxml" in BeautifulSoup.__dict__ else "html.parser")
            if body_soup.body:
                # 移除脚本和样式
                for tag in body_soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                content_text = body_soup.body.get_text(separator="\n", strip=True)
                content_text = clean_html(content_text)

        truncated = truncate_text(content_text, max_length)

        return {
            "url": url,
            "title": title.strip(),
            "content": truncated,
            "text_length": len(truncated),
            "error": None,
        }

    except httpx.TimeoutException:
        return {"url": url, "title": "", "content": "", "text_length": 0, "error": f"请求超时 ({timeout}s)"}
    except httpx.HTTPStatusError as e:
        return {"url": url, "title": "", "content": "", "text_length": 0, "error": f"HTTP 错误: {e.response.status_code}"}
    except Exception as e:
        return {"url": url, "title": "", "content": "", "text_length": 0, "error": f"抓取失败: {str(e)}"}


async def fetch_article_content(
    urls: list[str],
    max_length: int = 3000,
    db: Optional[NewsDatabase] = None,
) -> dict:
    """
    批量抓取网页正文

    Returns:
        {
            "total": int,            # 总数
            "success": int,          # 成功数
            "failed": int,           # 失败数
            "articles": [...]        # 文章内容列表
        }
    """
    if not urls:
        return {"total": 0, "success": 0, "failed": 0, "articles": []}

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    # 限制并发数
    semaphore = asyncio.Semaphore(5)

    async def fetch_with_semaphore(client, url):
        async with semaphore:
            return await fetch_single_content(client, url, max_length, timeout=20)

    async with httpx.AsyncClient(headers=headers, http2=True) as client:
        tasks = [fetch_with_semaphore(client, url) for url in urls[:10]]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    articles = []
    success = 0
    failed = 0

    for i, result in enumerate(results):
        url = urls[i]
        if isinstance(result, Exception):
            failed += 1
            articles.append({"url": url, "title": "", "content": "", "text_length": 0, "error": str(result)})
        else:
            if result["error"]:
                failed += 1
            else:
                success += 1
                # 更新数据库中的正文
                if db and result["content"]:
                    await db.update_article_content(url, result["content"])
            articles.append(result)

    return {
        "total": len(urls),
        "success": success,
        "failed": failed,
        "articles": articles,
    }
