from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.news.models import (
    NewsArticle,
)


async def list_articles(db, limit, offset):
    stmt = select(NewsArticle).order_by(NewsArticle.publish_date.desc()).limit(limit).offset(offset)
    result = await db.exec(stmt)
    articles = result.all()

    count_result = await db.exec(select(func.count()).select_from(NewsArticle))
    total = count_result.one()
    return articles, total


async def recent_articles(db, limit: int):
    stmt = select(NewsArticle).order_by(NewsArticle.publish_date.desc()).limit(limit)
    result = await db.exec(stmt)
    articles = list(result.all())
    return articles


async def find_article(db: AsyncSession, article_id: int) -> NewsArticle | None:
    return await db.get(NewsArticle, article_id)


async def find_article_by_url(db: AsyncSession, url: str) -> NewsArticle | None:
    stmt = select(NewsArticle).where(NewsArticle.url == url)
    result = await db.exec(stmt)
    existing_article = result.first()
    return existing_article


def add_article(db: AsyncSession, article: NewsArticle) -> None:
    db.add(article)


async def refresh_article(db: AsyncSession, article: NewsArticle) -> None:
    await db.refresh(article)


async def existing_urls(db: AsyncSession, urls: list[str]) -> set[str]:
    existing: set[str] = set()
    chunk_size = 500
    for index in range(0, len(urls), chunk_size):
        chunk = urls[index : index + chunk_size]
        result = await db.exec(select(NewsArticle.url).where(NewsArticle.url.in_(chunk)))
        existing.update(result.all())
    return existing


def add_articles(db: AsyncSession, articles: list[NewsArticle]) -> None:
    db.add_all(articles)


async def articles_by_urls(db: AsyncSession, urls: set[str]) -> list[NewsArticle]:
    result = await db.exec(select(NewsArticle).where(NewsArticle.url.in_(urls)))
    return list(result.all())
