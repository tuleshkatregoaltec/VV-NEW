import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.cache import invalidate_prefixes
from app.news import repository
from app.news.models import (
    NewsArticle,
    NewsArticleCreate,
    NewsArticleResponse,
    NewsArticleUploadResponse,
)


async def create_news_article(
    db: AsyncSession,
    article_data: NewsArticleCreate,
) -> NewsArticleUploadResponse:
    existing_article = await repository.find_article_by_url(db, article_data.url)

    if existing_article:
        return NewsArticleUploadResponse(
            created=False,
            article=NewsArticleResponse(
                id=existing_article.id,
                url=existing_article.url,
                headline=existing_article.headline,
                thumbnail_url=existing_article.thumbnail_url or "",
                thumbnail_base64=existing_article.thumbnail_base64,
                publish_date=existing_article.publish_date.replace(tzinfo=timezone.utc),
            ),
            message="Article already exists in database",
        )

    news_article = NewsArticle(**article_data.model_dump())

    news_article.publish_date = news_article.publish_date.replace(tzinfo=timezone.utc)

    repository.add_article(db, news_article)
    await db.commit()
    await repository.refresh_article(db, news_article)
    await invalidate_prefixes(("news",))

    return NewsArticleUploadResponse(
        created=True,
        article=NewsArticleResponse(
            id=news_article.id,
            url=news_article.url,
            headline=news_article.headline,
            thumbnail_url=news_article.thumbnail_url or "",
            thumbnail_base64=news_article.thumbnail_base64,
            publish_date=news_article.publish_date.replace(tzinfo=timezone.utc),
        ),
        message="Article created successfully",
    )


NEWS_THUMBNAIL_PREFIX = "media/news/thumbnails/"


@dataclass
class ImportStats:
    fetched: int = 0
    normalized: int = 0
    skipped: int = 0
    existing: int = 0
    inserted: int = 0
    updated: int = 0


def pick_string(item: dict[str, Any], *field_names: str) -> str | None:
    for field_name in field_names:
        value = item.get(field_name)
        if value is None:
            continue
        if isinstance(value, dict):
            value = value.get("url") or value.get("href") or value.get("src")
        if isinstance(value, list):
            value = next((entry for entry in value if entry), None)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, int | float):
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = parsedate_to_datetime(text)
            except (TypeError, ValueError):
                return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def clip_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3].rstrip() + "..."


def normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().rstrip("/") or None


def normalize_article(item: dict[str, Any]) -> NewsArticle | None:
    url = pick_string(item, "canonical_url", "url", "link", "guid")
    headline = pick_string(item, "headline", "title", "name")
    if not url or not headline or len(url) > 255:
        return None

    thumbnail_url = pick_string(
        item,
        "thumbnail_url",
        "thumbnail_source_url",
        "image_url",
        "image",
        "thumbnail",
    )
    if thumbnail_url and len(thumbnail_url) > 255:
        thumbnail_url = None

    return NewsArticle(
        url=url,
        headline=clip_text(headline, 255),
        thumbnail_url=thumbnail_url,
        thumbnail_base64=pick_string(item, "thumbnail_base64"),
        publish_date=parse_datetime(
            item.get("publish_date")
            or item.get("published_at")
            or item.get("published")
            or item.get("date")
            or item.get("discovered_at")
        ),
    )


def records_from_json(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("articles", "items", "results", "data"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [value]
    return []


def parse_records(data: bytes) -> tuple[list[dict[str, Any]], int]:
    text = data.decode("utf-8-sig")
    stripped = text.lstrip()
    if not stripped:
        return [], 0

    if stripped.startswith(("[", "{")):
        try:
            return records_from_json(json.loads(stripped)), 0
        except json.JSONDecodeError:
            if stripped.startswith("["):
                raise

    records: list[dict[str, Any]] = []
    malformed = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(value, dict):
            records.append(value)
        else:
            malformed += 1
    return records, malformed


def dedupe_articles(articles: list[NewsArticle]) -> list[NewsArticle]:
    deduped: dict[str, NewsArticle] = {}
    for article in articles:
        deduped[article.url] = article
    return list(deduped.values())


def thumbnail_target_key(item: dict[str, Any]) -> str | None:
    target_key = pick_string(item, "target_key")
    if target_key and target_key.startswith(NEWS_THUMBNAIL_PREFIX):
        return target_key
    return None


def build_thumbnail_map(records: list[dict[str, Any]]) -> dict[str, str]:
    thumbnail_map: dict[str, str] = {}
    for item in records:
        target_key = thumbnail_target_key(item)
        if not target_key:
            continue
        for field_name in ("canonical_url", "normalized_url", "url"):
            url = normalize_url(pick_string(item, field_name))
            if url:
                thumbnail_map[url] = target_key
    return thumbnail_map


def apply_thumbnail_map(articles: list[NewsArticle], thumbnail_map: dict[str, str]) -> int:
    applied = 0
    for article in articles:
        target_key = thumbnail_map.get(normalize_url(article.url) or "")
        if not target_key:
            continue
        article.thumbnail_url = target_key
        applied += 1
    return applied


async def import_articles(
    db: AsyncSession, articles: list[NewsArticle], update_existing: bool
) -> ImportStats:
    stats = ImportStats(normalized=len(articles))
    urls = [article.url for article in articles]
    current_urls = await repository.existing_urls(db, urls)
    stats.existing = len(current_urls)

    to_insert = [article for article in articles if article.url not in current_urls]
    repository.add_articles(db, to_insert)
    stats.inserted = len(to_insert)

    if update_existing and current_urls:
        existing_articles = await repository.articles_by_urls(db, current_urls)
        existing_by_url = {article.url: article for article in existing_articles}
        incoming_by_url = {article.url: article for article in articles}
        for url, existing_article in existing_by_url.items():
            incoming = incoming_by_url[url]
            existing_article.headline = incoming.headline
            existing_article.thumbnail_url = incoming.thumbnail_url
            existing_article.thumbnail_base64 = incoming.thumbnail_base64
            existing_article.publish_date = incoming.publish_date
            repository.add_article(db, existing_article)
            stats.updated += 1

    await db.commit()

    return stats
