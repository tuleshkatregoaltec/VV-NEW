from uuid import uuid4

from app.config import settings
from app.core.cache import RedisCache


async def test_redis_roundtrip_and_prefix_cleanup_are_real():
    cache = RedisCache(settings.REDIS_URL, settings.REDIS_PASSWORD)
    prefix = "vv_migration_test:" + uuid4().hex + ":"
    try:
        assert await cache.ping()
        await cache.set(prefix + "one", {"count": 1}, expire=30)
        assert await cache.get(prefix + "one") == {"count": 1}
        await cache.clear_prefix(prefix)
        assert await cache.get(prefix + "one") is None
    finally:
        await cache.clear_prefix(prefix)
        if cache.redis is not None:
            await cache.redis.aclose()


async def test_chunk_storage_metadata_and_thumbnail_signing_use_real_minio(monkeypatch):
    import io
    import json
    from urllib.parse import urlparse

    import httpx
    from fastapi import UploadFile

    from app.core.cache import invalidate_prefixes
    from app.core.object_storage import ObjectStorageClient
    from app.news.service import signed_news_thumbnail_url
    from app.upload import service

    endpoint = str(settings.OBJECT_STORAGE_ENDPOINT_URL)
    assert urlparse(endpoint).hostname in {"localhost", "127.0.0.1", "::1"}
    identifier = uuid4().hex
    bucket = "vv-migration-test-" + identifier
    storage = ObjectStorageClient(
        endpoint,
        settings.OBJECT_STORAGE_ACCESS_KEY_ID,
        settings.OBJECT_STORAGE_SECRET_ACCESS_KEY,
        bucket,
        settings.OBJECT_STORAGE_REGION,
        settings.OBJECT_STORAGE_USE_PATH_STYLE,
    )
    cache = RedisCache(settings.REDIS_URL, settings.REDIS_PASSWORD)
    monkeypatch.setattr(service, "object_storage_client", storage)
    monkeypatch.setattr(service, "cache", cache)
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", bucket)
    prefix = f"etl/uploads/{identifier}/"
    keys = [prefix + "chunk_0001", prefix + "chunk_0002", prefix + "metadata.json"]
    async with storage.client() as client:
        await client.create_bucket(Bucket=bucket)
    try:
        for index, body in [(1, b"hello"), (2, b"world")]:
            result = await service.process_chunked_upload(
                UploadFile(file=io.BytesIO(body), filename="chunk"),
                source_url="https://example.com/source",
                upload_session_id=identifier,
                chunk_index=index,
                total_chunks=2,
                original_filename="fixture.csv",
                total_file_size=10,
                chunk_size=5,
                expected_hash="caller-hash",
            )
            assert result.status == ("chunk_received" if index == 1 else "completed")
        assert await storage.download(keys[0]) == b"hello"
        metadata = json.loads(await storage.download(keys[2]))
        assert metadata["status"] == "pending"
        assert metadata["expected_hash"] == "caller-hash"
        assert metadata["total_chunks"] == 2
        assert metadata["session_id"] == identifier
        assert await cache.redis.exists("upload:" + identifier) == 0
        signed = await signed_news_thumbnail_url(keys[0])
        async with httpx.AsyncClient() as client:
            response = await client.get(signed)
            assert response.status_code == 200
            assert response.content == b"hello"
        cache_prefix = "vv_migration_test:" + identifier
        await cache.set(cache_prefix + ":one", {"value": 1})
        assert (
            await invalidate_prefixes((cache_prefix,), cache_client=cache, require_connection=True)
            == 1
        )
    finally:
        await storage.delete_many(keys)
        async with storage.client() as client:
            await client.delete_bucket(Bucket=bucket)
        await cache.delete("upload:" + identifier)
        await cache.clear_prefix("vv_migration_test:" + identifier)
        if cache.redis is not None:
            await cache.redis.aclose()
