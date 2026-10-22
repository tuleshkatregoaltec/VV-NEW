from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

from app.news import ingestion, service
from app.news.models import NewsArticle, NewsArticleCreate


async def test_upload_deduplicates_without_updating_and_batch_import_updates_only_when_requested(
    pg_session, monkeypatch
):
    invalidation = AsyncMock()
    monkeypatch.setattr(ingestion, "invalidate_prefixes", invalidation)
    url = f"https://example.com/{uuid4().hex}"
    first = await ingestion.create_news_article(
        pg_session,
        NewsArticleCreate(
            url=url,
            headline="Original",
            publish_date=datetime(2026, 9, 1),
            thumbnail_url="media/news/thumbnails/test",
        ),
    )
    assert first.created
    assert (
        first.article.thumbnail_url == "media/news/thumbnails/test"
    )  # Upload contract retains the stored key.
    invalidation.assert_awaited_once_with(("news",))
    duplicate = await ingestion.create_news_article(
        pg_session,
        NewsArticleCreate(url=url, headline="Replacement", publish_date=datetime(2026, 9, 2)),
    )
    assert not duplicate.created
    assert duplicate.article.headline == "Original"
    assert invalidation.await_count == 1
    incoming = NewsArticle(
        url=url, headline="Batch update", publish_date=datetime(2026, 9, 3, tzinfo=timezone.utc)
    )
    unchanged = await ingestion.import_articles(pg_session, [incoming], update_existing=False)
    assert (unchanged.existing, unchanged.inserted, unchanged.updated) == (1, 0, 0)
    updated = await ingestion.import_articles(pg_session, [incoming], update_existing=True)
    assert (updated.existing, updated.inserted, updated.updated) == (1, 0, 1)
    pg_session.expire_all()
    result = await service.fetch_news_articles(pg_session, limit=100)
    saved = next(article for article in result.articles if article.url == url)
    assert saved.headline == "Batch update"
    assert saved.thumbnail_url == ""
