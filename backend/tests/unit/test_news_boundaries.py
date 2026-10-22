from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app import config
from app.core import object_storage
from app.news import agent, service
from app.news.models import NewsArticle


def test_legacy_news_environment_precedence_is_name_then_process_cwd_parent(monkeypatch, tmp_path):
    child = tmp_path / "backend"
    child.mkdir()
    (tmp_path / ".env.dev").write_text("VV_NEWS_FIRST=parent\nVV_NEWS_SECOND=parent-second\n")
    (child / ".env.dev").write_text("VV_NEWS_FIRST=cwd\n")
    monkeypatch.chdir(child)
    monkeypatch.delenv("VV_NEWS_FIRST", raising=False)
    monkeypatch.setenv("VV_NEWS_SECOND", "process-second")
    assert config.env_value("VV_NEWS_FIRST", "VV_NEWS_SECOND") == "cwd"
    monkeypatch.setenv("VV_NEWS_FIRST", "process-first")
    assert config.env_value("VV_NEWS_FIRST", "VV_NEWS_SECOND") == "process-first"
    monkeypatch.setenv("VV_NEWS_FIRST", "")
    assert config.env_value("VV_NEWS_FIRST") == "cwd"


@pytest.mark.parametrize("awaitable", [False, True])
async def test_thumbnail_signer_preserves_expiry_provider_arguments_and_awaitability(
    monkeypatch, awaitable
):
    values = {
        "OBJECT_STORAGE_BUCKET": "fixture",
        "OBJECT_STORAGE_ACCESS_KEY_ID": "key",
        "OBJECT_STORAGE_SECRET_ACCESS_KEY": "secret",
        "OBJECT_STORAGE_REGION": "auto",
        "OBJECT_STORAGE_ENDPOINT_URL": "http://localhost:9002",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    signed = (
        AsyncMock(return_value="https://example.com/signed")
        if awaitable
        else Mock(return_value="https://example.com/signed")
    )
    calls = []

    @asynccontextmanager
    async def client(kind, **kwargs):
        calls.append((kind, kwargs))
        yield SimpleNamespace(generate_presigned_url=signed)

    monkeypatch.setattr(object_storage.aioboto3, "Session", lambda: SimpleNamespace(client=client))
    assert (
        await service.signed_news_thumbnail_url("media/news/thumbnails/key")
        == "https://example.com/signed"
    )
    assert calls == [
        (
            "s3",
            {
                "aws_access_key_id": "key",
                "aws_secret_access_key": "secret",
                "region_name": "auto",
                "endpoint_url": "http://localhost:9002",
            },
        )
    ]
    assert signed.call_args.args == ("get_object",)
    assert signed.call_args.kwargs == {
        "Params": {"Bucket": "fixture", "Key": "media/news/thumbnails/key"},
        "ExpiresIn": 86400,
    }


async def test_summary_provider_failure_preserves_headline_fallback_and_request_limit(monkeypatch):
    articles = [
        NewsArticle(
            id=1,
            url="https://example.com/a",
            headline="Dubai rents rise",
            publish_date=datetime.now(timezone.utc),
        ),
        NewsArticle(
            id=2,
            url="https://example.com/b",
            headline="Bank rates stable",
            publish_date=datetime.now(timezone.utc),
        ),
    ]
    db = SimpleNamespace(exec=AsyncMock(return_value=SimpleNamespace(all=lambda: articles)))
    run = AsyncMock(side_effect=RuntimeError("unavailable"))
    monkeypatch.setattr(agent, "get_news_summary_agent", lambda: SimpleNamespace(run=run))
    response = await service.fetch_news_summary(db)
    assert response.article_count == 2
    assert response.real_estate.bullets == ["Dubai rents rise"]
    assert response.macro.bullets == ["Bank rates stable"]
    assert run.call_args.kwargs["usage_limits"].request_limit == 1
    assert "Dubai rents rise" in run.call_args.args[0]


async def test_thumbnail_missing_article_retains_not_found():
    db = SimpleNamespace(get=AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as error:
        await service.fetch_news_thumbnail(db, 999)
    assert error.value.status_code == 404


def test_import_normalization_keeps_last_duplicate_and_thumbnail_manifest_rules():
    from app.news import ingestion

    records, malformed = ingestion.parse_records(
        b'{"url":"https://example.com/a","title":"First"}\ninvalid\n{"url":"https://example.com/a","title":"Last"}\n'
    )
    assert malformed == 1
    articles = ingestion.dedupe_articles(
        [ingestion.normalize_article(record) for record in records]
    )
    assert len(articles) == 1
    assert articles[0].headline == "Last"
    mapping = ingestion.build_thumbnail_map(
        [
            {"url": "https://example.com/a/", "target_key": "media/news/thumbnails/thumb"},
            {"url": "https://example.com/a", "target_key": "outside-prefix"},
        ]
    )
    assert ingestion.apply_thumbnail_map(articles, mapping) == 1
    assert articles[0].thumbnail_url == "media/news/thumbnails/thumb"
