from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aioboto3
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.cache import RedisCache, invalidate_prefixes  # noqa: E402
from app.news.ingestion import (  # noqa: E402
    ImportStats,
    apply_thumbnail_map,
    build_thumbnail_map,
    dedupe_articles,
    import_articles,
    normalize_article,
    parse_records,
)
from app.news.models import NewsArticle  # noqa: E402

DEFAULT_PREFIX = "raw/source=news_feeds/"
DEFAULT_ARTICLES_FILENAME = "news_feed_articles.jsonl"
DEFAULT_THUMBNAIL_MANIFEST_PREFIX = "raw/source=news_feeds/thumbnail_asset_manifests/"
NEWS_THUMBNAIL_PREFIX = "media/news/thumbnails/"


def load_env() -> None:
    for path in (Path.cwd() / ".env.dev", Path.cwd().parent / ".env.dev"):
        if path.exists():
            load_dotenv(path, override=False)
            return


def env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def require_env(*names: str) -> str:
    value = env_value(*names)
    if value:
        return value
    label = " or ".join(names)
    raise RuntimeError(f"Missing required environment variable: {label}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-off importer for raw object-storage news feed articles into news_articles."
    )
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument(
        "--key",
        help="Import a specific object-storage key instead of the latest feed.",
    )
    parser.add_argument("--filename", default=DEFAULT_ARTICLES_FILENAME)
    parser.add_argument(
        "--thumbnail-manifest-key",
        help="Import thumbnail object keys from a specific manifest object.",
    )
    parser.add_argument("--thumbnail-manifest-prefix", default=DEFAULT_THUMBNAIL_MANIFEST_PREFIX)
    parser.add_argument("--skip-thumbnail-manifest", action="store_true")
    parser.add_argument("--limit", type=int, help="Maximum normalized articles to insert.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--update-existing", action="store_true")
    return parser.parse_args()


def build_object_storage_config() -> dict[str, str]:
    return {
        "endpoint_url": env_value("OBJECT_STORAGE_ENDPOINT_URL") or "",
        "bucket": require_env("OBJECT_STORAGE_BUCKET"),
        "region": env_value("OBJECT_STORAGE_REGION") or "auto",
        "access_key": require_env("OBJECT_STORAGE_ACCESS_KEY_ID"),
        "secret_key": require_env("OBJECT_STORAGE_SECRET_ACCESS_KEY"),
    }


async def get_object_storage_client():
    config = build_object_storage_config()
    session = aioboto3.Session()
    client_kwargs = {
        "aws_access_key_id": config["access_key"],
        "aws_secret_access_key": config["secret_key"],
        "region_name": config["region"],
    }
    if config["endpoint_url"]:
        client_kwargs["endpoint_url"] = config["endpoint_url"]
    return session.client("s3", **client_kwargs)


async def find_latest_articles_key(s3: Any, bucket: str, prefix: str, filename: str) -> str:
    matches: list[dict[str, Any]] = []
    paginator = s3.get_paginator("list_objects_v2")
    async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(filename) and obj.get("Size", 0) > 0:
                matches.append(obj)

    if not matches:
        raise RuntimeError(f"No non-empty {filename} objects found under {prefix}")

    latest = max(
        matches,
        key=lambda obj: obj.get("LastModified") or datetime.min.replace(tzinfo=timezone.utc),
    )
    return latest["Key"]


async def find_latest_thumbnail_manifest_key(s3: Any, bucket: str, prefix: str) -> str | None:
    matches: list[dict[str, Any]] = []
    paginator = s3.get_paginator("list_objects_v2")
    async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("news_thumbnail_assets.jsonl") and obj.get("Size", 0) > 0:
                matches.append(obj)

    if not matches:
        return None

    latest = max(
        matches,
        key=lambda obj: obj.get("LastModified") or datetime.min.replace(tzinfo=timezone.utc),
    )
    return latest["Key"]


async def download_records_object(key: str) -> tuple[list[dict[str, Any]], int]:
    bucket = require_env("OBJECT_STORAGE_BUCKET")
    async with await get_object_storage_client() as s3:
        response = await s3.get_object(Bucket=bucket, Key=key)
        data = await response["Body"].read()
    return parse_records(data)


def get_database_url() -> str:
    return require_env("POSTGRES_URL")


async def upsert_articles(articles: list[NewsArticle], update_existing: bool) -> ImportStats:
    stats = ImportStats(normalized=len(articles))
    engine = create_async_engine(get_database_url(), echo=False, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with session_factory() as db:
            stats = await import_articles(db, articles, update_existing)
    finally:
        await engine.dispose()

    return stats


async def clear_news_cache() -> int:
    redis_url = env_value("REDIS_URL")
    redis_password = env_value("REDIS_PASSWORD") or ""
    if not redis_url:
        return 0
    cache = RedisCache(redis_url, redis_password)
    return await invalidate_prefixes(("news",), cache_client=cache)


async def main() -> int:
    load_env()
    args = parse_args()
    object_storage_config = build_object_storage_config()

    key = args.key
    if not key:
        async with await get_object_storage_client() as s3:
            key = await find_latest_articles_key(
                s3,
                object_storage_config["bucket"],
                args.prefix,
                args.filename,
            )

    records, malformed = await download_records_object(key)
    normalized = [article for item in records if (article := normalize_article(item)) is not None]
    articles = dedupe_articles(normalized)

    thumbnail_manifest_key = args.thumbnail_manifest_key
    thumbnails_applied = 0
    if not args.skip_thumbnail_manifest:
        if not thumbnail_manifest_key:
            async with await get_object_storage_client() as s3:
                thumbnail_manifest_key = await find_latest_thumbnail_manifest_key(
                    s3,
                    object_storage_config["bucket"],
                    args.thumbnail_manifest_prefix,
                )
        if thumbnail_manifest_key:
            thumbnail_records, thumbnail_malformed = await download_records_object(
                thumbnail_manifest_key
            )
            malformed += thumbnail_malformed
            thumbnails_applied = apply_thumbnail_map(
                articles,
                build_thumbnail_map(thumbnail_records),
            )

    if args.limit is not None:
        articles = articles[: args.limit]

    stats = ImportStats(
        fetched=len(records),
        normalized=len(articles),
        skipped=len(records) - len(normalized) + malformed,
    )

    print(f"source_key={key}")
    if thumbnail_manifest_key:
        print(f"thumbnail_manifest_key={thumbnail_manifest_key}")
    print(
        f"fetched={stats.fetched} normalized={stats.normalized} "
        f"skipped={stats.skipped} thumbnails_applied={thumbnails_applied} dry_run={args.dry_run}"
    )

    for article in articles[:5]:
        date = article.publish_date.isoformat() if article.publish_date else ""
        print(f"preview\t{date}\t{article.headline}\t{article.url}")

    if args.dry_run:
        return 0

    db_stats = await upsert_articles(articles, args.update_existing)
    cache_deleted = await clear_news_cache()
    print(
        f"inserted={db_stats.inserted} existing={db_stats.existing} "
        f"updated={db_stats.updated} cache_deleted={cache_deleted}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
