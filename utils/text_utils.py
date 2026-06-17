"""
文本处理工具 — 截断、清洗、HTML 生成
"""
import re
from datetime import datetime, timezone
from typing import Optional


def truncate_text(text: str, max_length: int = 3000) -> str:
    """截断文本到指定长度，尽量在句子边界处截断"""
    if not text or len(text) <= max_length:
        return text or ""

    truncated = text[:max_length]
    # 尝试在最后一个句号处截断
    last_period = truncated.rfind(".")
    last_chinese_period = truncated.rfind("。")
    last_newline = truncated.rfind("\n")

    cut_point = max(last_period, last_chinese_period, last_newline)
    if cut_point > max_length * 0.7:
        return truncated[: cut_point + 1]

    return truncated + "..."


def clean_html(raw_html: str) -> str:
    """清洗 HTML 标签，保留纯文本"""
    if not raw_html:
        return ""
    # 移除 script 和 style
    clean = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
    # 移除 HTML 标签
    clean = re.sub(r"<[^>]+>", "", clean)
    # 压缩空白
    clean = re.sub(r"\s+", " ", clean)
    # 解码常见 HTML 实体
    clean = clean.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    clean = clean.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    return clean.strip()


def extract_keywords(text: str, max_keywords: int = 5) -> list[str]:
    """从文本中提取关键词（简单 TF 方法）"""
    # 这是一个轻量级实现，实际使用中可替换为更复杂的方法
    words = re.findall(r"[\w一-鿿]{2,}", text.lower())
    # 停用词
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "between", "and", "but", "or",
        "nor", "not", "so", "yet", "both", "either", "neither", "each", "every",
        "all", "any", "few", "more", "most", "other", "some", "such", "no",
        "only", "own", "same", "than", "too", "very", "this", "that", "these",
        "those", "it", "its", "they", "them", "their", "he", "she", "his", "her",
        "just", "about", "over", "also", "new", "one", "two", "now", "like",
        "get", "make", "made", "use", "used", "using", "way", "set",
    }
    filtered = [w for w in words if w not in stopwords]

    # 简单频率统计
    freq: dict[str, int] = {}
    for w in filtered:
        freq[w] = freq.get(w, 0) + 1

    return sorted(freq, key=freq.get, reverse=True)[:max_keywords]


def format_datetime(iso_str: Optional[str]) -> str:
    """格式化 ISO 时间为可读形式"""
    if not iso_str:
        return "未知时间"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        # 转为北京时间
        bj_tz = timezone(offset=__import__("datetime").timedelta(hours=8))
        dt_bj = dt.astimezone(bj_tz)
        return dt_bj.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso_str[:19] if len(iso_str) >= 19 else iso_str
