"""Хранилище MinIO: три бакета, ключи `{entity_type}/{entity_id}/{file_id}/{safe_name}`.

deployment.md §3.8: бакеты `crm-files` (вложения), `crm-imports` (исходники и отчёты
импорта), `crm-exports` (выгрузки). Клиент `minio` синхронный — все вызовы уводятся
в поток (`asyncio.to_thread`), чтобы не блокировать event loop. Прямого анонимного
доступа к MinIO нет: наружу файлы отдаёт backend (proxy) либо presigned URL TTL 10 мин.
"""

from __future__ import annotations

import asyncio
import io
import re
import unicodedata
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from urllib.parse import quote, urlparse

from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings
from app.core.errors import ApiError

PRESIGNED_TTL = timedelta(minutes=10)
_STREAM_CHUNK = 512 * 1024

_SAFE_NAME_RE = re.compile(r"[^\w.\-]+", flags=re.UNICODE)


def storage_unavailable(detail: str = "") -> ApiError:
    return ApiError(
        502,
        "integration_unavailable",
        "Файловое хранилище недоступно, повторите позже"
        + (f" ({detail})" if detail else ""),
    )


def safe_file_name(original: str) -> str:
    """Безопасное имя в ключе S3: без путей/спецсимволов, ≤100 символов, NFC."""
    name = unicodedata.normalize("NFC", original or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _SAFE_NAME_RE.sub("_", name).strip("._")
    if len(name) > 100:
        stem, _, ext = name.rpartition(".")
        name = (stem[: 100 - len(ext) - 1] + "." + ext) if ext and stem else name[:100]
    return name or "file"


def build_s3_key(entity_type: str, entity_id: uuid.UUID, file_id: uuid.UUID, name: str) -> str:
    return f"{entity_type}/{entity_id}/{file_id}/{safe_file_name(name)}"


def content_disposition(filename: str) -> str:
    """`attachment` c ASCII-фолбэком и RFC 5987 для кириллических имён."""
    ascii_name = filename.encode("ascii", "ignore").decode() or "file"
    ascii_name = ascii_name.replace('"', "")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


class S3Storage:
    """Тонкая async-обёртка над Minio-клиентом с ленивым созданием бакетов."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str) -> None:
        parsed = urlparse(endpoint if "//" in endpoint else f"http://{endpoint}")
        self._client = Minio(
            parsed.netloc,
            access_key=access_key,
            secret_key=secret_key,
            secure=parsed.scheme == "https",
        )
        self._known_buckets: set[str] = set()

    async def _ensure_bucket(self, bucket: str) -> None:
        if bucket in self._known_buckets:
            return

        def _make() -> None:
            if not self._client.bucket_exists(bucket):
                self._client.make_bucket(bucket)

        await asyncio.to_thread(_make)
        self._known_buckets.add(bucket)

    async def put_bytes(
        self, bucket: str, key: str, data: bytes, content_type: str | None
    ) -> None:
        await self._ensure_bucket(bucket)

        def _put() -> None:
            self._client.put_object(
                bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type or "application/octet-stream",
            )

        try:
            await asyncio.to_thread(_put)
        except (S3Error, OSError) as exc:
            raise storage_unavailable(str(exc)[:120]) from exc

    async def read_bytes(self, bucket: str, key: str) -> bytes:
        def _read() -> bytes:
            response = self._client.get_object(bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        try:
            return await asyncio.to_thread(_read)
        except (S3Error, OSError) as exc:
            raise storage_unavailable(str(exc)[:120]) from exc

    async def stream(self, bucket: str, key: str) -> AsyncIterator[bytes]:
        """Чанковая отдача объекта (режим FILE_DELIVERY=proxy)."""
        try:
            response = await asyncio.to_thread(self._client.get_object, bucket, key)
        except (S3Error, OSError) as exc:
            raise storage_unavailable(str(exc)[:120]) from exc
        try:
            while True:
                chunk = await asyncio.to_thread(response.read, _STREAM_CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(response.close)
            response.release_conn()

    async def presigned_url(self, bucket: str, key: str, download_name: str) -> str:
        def _sign() -> str:
            return self._client.presigned_get_object(
                bucket,
                key,
                expires=PRESIGNED_TTL,
                response_headers={
                    "response-content-disposition": content_disposition(download_name)
                },
            )

        try:
            return await asyncio.to_thread(_sign)
        except (S3Error, OSError) as exc:
            raise storage_unavailable(str(exc)[:120]) from exc

    async def remove(self, bucket: str, key: str) -> bool:
        """Удаление объекта; ошибки не фатальны (чистка — фоновая, best effort)."""
        try:
            await asyncio.to_thread(self._client.remove_object, bucket, key)
            return True
        except (S3Error, OSError):
            return False


_storage: S3Storage | None = None


def get_storage() -> S3Storage:
    global _storage
    if _storage is None:
        settings = get_settings()
        _storage = S3Storage(settings.s3_endpoint, settings.s3_access_key, settings.s3_secret_key)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
