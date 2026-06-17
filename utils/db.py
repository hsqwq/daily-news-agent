"""
SQLite 数据库层 — 文章去重、缓存和状态管理
"""
import hashlib
import aiosqlite
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


class NewsDatabase:
    """新闻文章持久化存储，提供去重和缓存能力"""

    def __init__(self, db_path: str = "data/news.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def init(self):
        """初始化数据库表"""
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url_hash TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    language TEXT DEFAULT 'en',
                    summary TEXT,
                    content TEXT,
                    published_at TEXT,
                    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
                    is_read INTEGER DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_url_hash ON articles(url_hash)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_published_at ON articles(published_at)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_category ON articles(category)
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS feed_state (
                    feed_url TEXT PRIMARY KEY,
                    last_fetched_at TEXT,
                    last_etag TEXT,
                    fetch_count INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    last_error TEXT
                )
            """)
            await db.commit()

    @staticmethod
    def make_url_hash(url: str) -> str:
        """为 URL 生成 SHA256 哈希用于去重"""
        return hashlib.sha256(url.strip().lower().encode()).hexdigest()[:16]

    async def article_exists(self, url: str) -> bool:
        """检查文章是否已存在于数据库中"""
        url_hash = self.make_url_hash(url)
        async with aiosqlite.connect(str(self.db_path)) as db:
            cursor = await db.execute(
                "SELECT 1 FROM articles WHERE url_hash = ?", (url_hash,)
            )
            return await cursor.fetchone() is not None

    async def insert_article(
        self,
        title: str,
        url: str,
        source_name: str,
        category: str,
        language: str = "en",
        summary: Optional[str] = None,
        content: Optional[str] = None,
        published_at: Optional[str] = None,
    ) -> bool:
        """
        插入新文章。如果 URL 已存在（去重），则跳过。
        返回 True 表示插入成功（是新文章），False 表示重复跳过。
        """
        url_hash = self.make_url_hash(url)
        async with aiosqlite.connect(str(self.db_path)) as db:
            try:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO articles
                        (url_hash, title, url, source_name, category, language, summary, content, published_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (url_hash, title, url, source_name, category, language, summary, content, published_at),
                )
                await db.commit()
                return db.total_changes > 0
            except Exception:
                return False

    async def batch_insert_articles(self, articles: list[dict]) -> int:
        """批量插入文章，返回新插入的数量"""
        inserted = 0
        for article in articles:
            if await self.insert_article(**article):
                inserted += 1
        return inserted

    async def get_articles(
        self,
        categories: Optional[list[str]] = None,
        since_hours: int = 24,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        """按分类和时间范围查询文章"""
        since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()

        query = """
            SELECT id, title, url, source_name, category, language,
                   summary, published_at, fetched_at
            FROM articles
            WHERE fetched_at >= ?
        """
        params: list = [since]

        if categories and "all" not in categories:
            placeholders = ",".join(["?" for _ in categories])
            query += f" AND category IN ({placeholders})"
            params.extend(categories)

        query += " ORDER BY published_at DESC, fetched_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def search_articles(self, keywords: list[str], limit: int = 50) -> list[dict]:
        """按关键词搜索文章"""
        conditions = " OR ".join(["title LIKE ?" for _ in keywords])
        params = [f"%{kw}%" for kw in keywords]

        query = f"""
            SELECT id, title, url, source_name, category, language,
                   summary, published_at, fetched_at
            FROM articles
            WHERE {conditions}
            ORDER BY published_at DESC
            LIMIT ?
        """
        params.append(limit)

        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def update_article_content(self, url: str, content: str) -> bool:
        """更新文章的正文内容"""
        url_hash = self.make_url_hash(url)
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute(
                "UPDATE articles SET content = ?, is_read = 1 WHERE url_hash = ?",
                (content, url_hash),
            )
            await db.commit()
            return db.total_changes > 0

    async def update_feed_state(self, feed_url: str, etag: Optional[str] = None, error: Optional[str] = None):
        """更新 RSS 源状态"""
        async with aiosqlite.connect(str(self.db_path)) as db:
            if error:
                await db.execute(
                    """
                    INSERT INTO feed_state (feed_url, last_fetched_at, error_count, last_error)
                    VALUES (?, datetime('now'), 1, ?)
                    ON CONFLICT(feed_url) DO UPDATE SET
                        error_count = error_count + 1,
                        last_error = ?,
                        last_fetched_at = datetime('now')
                    """,
                    (feed_url, error, error),
                )
            else:
                await db.execute(
                    """
                    INSERT INTO feed_state (feed_url, last_fetched_at, last_etag, fetch_count, error_count, last_error)
                    VALUES (?, datetime('now'), ?, 1, 0, NULL)
                    ON CONFLICT(feed_url) DO UPDATE SET
                        last_fetched_at = datetime('now'),
                        last_etag = COALESCE(?, last_etag),
                        fetch_count = fetch_count + 1,
                        error_count = 0,
                        last_error = NULL
                    """,
                    (feed_url, etag, etag),
                )
            await db.commit()

    async def cleanup_old_articles(self, retention_days: int = 7):
        """清理过期文章"""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute("DELETE FROM articles WHERE fetched_at < ?", (cutoff,))
            await db.commit()

    async def get_stats(self) -> dict:
        """获取数据库统计信息"""
        async with aiosqlite.connect(str(self.db_path)) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM articles")
            total = (await cursor.fetchone())[0]
            cursor = await db.execute(
                "SELECT category, COUNT(*) as cnt FROM articles GROUP BY category ORDER BY cnt DESC"
            )
            by_category = {row[0]: row[1] for row in await cursor.fetchall()}
            return {"total_articles": total, "by_category": by_category}
