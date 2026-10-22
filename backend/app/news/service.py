from datetime import timezone

from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import build_object_storage_client_kwargs, require_env, settings
from app.core.object_storage import ObjectStorageSigningError, sign_object_url
from app.news import repository
from app.news.agent import (
    summarize_articles,
)
from app.news.models import (
    NewsArticle,
    NewsArticleResponse,
    NewsListResponse,
    NewsSummaryResponse,
)

NEWS_THUMBNAIL_PREFIX = "media/news/thumbnails/"

NEWS_THUMBNAIL_SIGNED_URL_TTL_SECONDS = 60 * 60 * 24

NEWS_SUMMARY_ARTICLE_LIMIT = 50


def is_news_thumbnail_key(value: str | None) -> bool:
    return bool(value and value.startswith(NEWS_THUMBNAIL_PREFIX))


def news_thumbnail_url(article_id: int) -> str:
    base_url = str(settings.PUBLIC_API_BASE_URL).rstrip("/") if settings.PUBLIC_API_BASE_URL else ""
    return f"{base_url}/api/v1/news/articles/{article_id}/thumbnail"


def article_to_response(article: NewsArticle) -> NewsArticleResponse:
    publish_date = article.publish_date
    if publish_date is not None and publish_date.tzinfo is None:
        publish_date = publish_date.replace(tzinfo=timezone.utc)

    thumbnail_url = article.thumbnail_url or ""
    if is_news_thumbnail_key(thumbnail_url):
        thumbnail_url = news_thumbnail_url(article.id)

    return NewsArticleResponse(
        id=article.id,
        url=article.url,
        headline=article.headline,
        thumbnail_url=thumbnail_url,
        thumbnail_base64=article.thumbnail_base64,
        publish_date=publish_date,
    )


async def fetch_news_articles(
    db: AsyncSession,
    limit: int = 20,
    offset: int = 0,
) -> NewsListResponse:
    """Fetch paginated news articles sorted by publish date."""
    articles, total = await repository.list_articles(db, limit, offset)

    return NewsListResponse(
        articles=[article_to_response(article) for article in articles],
        total=total,
        limit=limit,
        offset=offset,
    )


async def fetch_news_summary(db: AsyncSession) -> NewsSummaryResponse:
    articles = await repository.recent_articles(db, NEWS_SUMMARY_ARTICLE_LIMIT)

    return await summarize_articles(articles)


async def signed_news_thumbnail_url(key: str) -> str:
    bucket = require_env("OBJECT_STORAGE_BUCKET")
    kwargs = build_object_storage_client_kwargs()
    try:
        return await sign_object_url(
            bucket=bucket,
            key=key,
            expires_in=NEWS_THUMBNAIL_SIGNED_URL_TTL_SECONDS,
            client_kwargs=kwargs,
        )
    except ObjectStorageSigningError as exc:
        raise HTTPException(status_code=404, detail="Thumbnail not found") from exc


async def fetch_news_thumbnail(
    db: AsyncSession,
    article_id: int,
) -> str:
    article = await repository.find_article(db, article_id)
    if article is None or not is_news_thumbnail_key(article.thumbnail_url):
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return await signed_news_thumbnail_url(article.thumbnail_url)
