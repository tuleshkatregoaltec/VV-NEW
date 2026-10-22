import hashlib
import logging
from datetime import datetime

from fastapi import HTTPException, UploadFile

from app.config import settings
from app.core.cache import RedisCache
from app.core.object_storage import ObjectStorageClient
from app.upload.models import ChunkedUploadResponse

logger = logging.getLogger(__name__)

object_storage_client = ObjectStorageClient(
    endpoint_url=(
        str(settings.OBJECT_STORAGE_ENDPOINT_URL) if settings.OBJECT_STORAGE_ENDPOINT_URL else None
    ),
    access_key_id=settings.OBJECT_STORAGE_ACCESS_KEY_ID,
    secret_access_key=settings.OBJECT_STORAGE_SECRET_ACCESS_KEY,
    bucket_name=settings.OBJECT_STORAGE_BUCKET,
    region=settings.OBJECT_STORAGE_REGION,
    use_path_style=settings.OBJECT_STORAGE_USE_PATH_STYLE,
)

cache = RedisCache(settings.REDIS_URL, settings.REDIS_PASSWORD)


def sanitize_filename(filename: str) -> str:
    safe = "".join(c for c in filename if c.isalnum() or c in "._-")
    if not safe or ".." in safe:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return safe


def calculate_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def process_chunked_upload(
    chunk: UploadFile,
    source_url: str,
    upload_session_id: str,
    chunk_index: int,
    total_chunks: int,
    original_filename: str,
    total_file_size: int,
    chunk_size: int,
    expected_hash: str | None = None,
) -> ChunkedUploadResponse:
    """
    Store chunk to object storage. Assembly is handled by the ETL node, not here.

    When all chunks are received, saves metadata.json with upload info
    for the ETL node to use during assembly.
    """
    if not 1 <= chunk_index <= total_chunks:
        raise HTTPException(400, f"Invalid chunk_index: {chunk_index}")

    safe_filename = sanitize_filename(original_filename)

    chunk_data = await chunk.read()

    # Store chunk in etl/uploads/ path for ETL node to access
    s3_key = f"etl/uploads/{upload_session_id}/chunk_{chunk_index:04d}"
    await object_storage_client.upload(s3_key, chunk_data)

    if not cache.redis:
        raise HTTPException(503, "Redis unavailable")

    redis_key = f"upload:{upload_session_id}"
    await cache.redis.sadd(redis_key, chunk_index)  # type: ignore
    await cache.redis.expire(redis_key, 3600)  # type: ignore

    logger.info(
        f"Chunk {chunk_index}/{total_chunks} uploaded to object storage for {safe_filename}"
    )

    received_count = await cache.redis.scard(redis_key)  # type: ignore

    if received_count < total_chunks:
        return ChunkedUploadResponse(
            status="chunk_received",
            chunk_index=chunk_index,
            total_chunks=total_chunks,
        )

    # All chunks received - save metadata for ETL node (no assembly here)
    logger.info(f"All {total_chunks} chunks received for {safe_filename}, saving metadata")

    try:
        import json

        metadata = {
            "session_id": upload_session_id,
            "filename": safe_filename,
            "source_url": source_url,
            "total_chunks": total_chunks,
            "total_size": total_file_size,
            "chunk_size": chunk_size,
            "expected_hash": expected_hash,
            "uploaded_at": datetime.now().isoformat(),
            "status": "pending",
        }

        metadata_key = f"etl/uploads/{upload_session_id}/metadata.json"
        await object_storage_client.upload(metadata_key, json.dumps(metadata).encode())

        await cache.delete(redis_key)

        logger.info(
            f"Metadata saved for {safe_filename} ({total_file_size:,} bytes, {total_chunks} chunks)"
        )

        return ChunkedUploadResponse(
            status="completed",
            filename=safe_filename,
            size=total_file_size,
            hash=expected_hash,
            source_url=source_url,
            uploaded_at=datetime.now().isoformat(),
            session_id=upload_session_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to save metadata for {safe_filename}")
        raise HTTPException(500, f"Metadata save failed: {e}")
