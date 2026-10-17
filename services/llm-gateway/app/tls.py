"""TLS до GigaChat: certifi + корневые сертификаты НУЦ Минцифры.

Цепочка *.devices.sberbank.ru подписана Russian Trusted Root CA, которого нет в certifi
и в системных хранилищах вне РФ-сборок. PEM-бандл (Root + Sub CA) монтируется из
ConfigMap llm-gateway-ca; в репозитории — deploy/k8s/base/llm-gateway/certs/.
Файлы из Windows-окружений приходят с CRLF — нормализуем в LF перед загрузкой.

Отсутствующий файл не роняет старт: в лог — предупреждение, в /readyz — ca_bundle=missing
(шлюз продолжит отвечать, а ошибка TLS проявится как llm_upstream_error в конкретном вызове).
"""
from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from pathlib import Path

import certifi
import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TlsSetup:
    # ssl.SSLContext — штатно; False — проверка выключена (только dev, см. config)
    verify: ssl.SSLContext | bool
    # loaded | missing | invalid | not_set
    ca_bundle: str


def load_ca_bundle(context: ssl.SSLContext, path: str) -> str:
    """Добавить PEM-бандл в контекст; вернуть статус для /readyz."""
    if not path:
        return "not_set"
    try:
        raw = Path(path).read_bytes()
    except OSError:
        logger.warning(
            "бандл сертификатов НУЦ не найден — GigaChat может не пройти проверку TLS",
            extra={"ca_bundle_path": path},
        )
        return "missing"
    try:
        text = raw.replace(b"\r\n", b"\n").decode("ascii")
        context.load_verify_locations(cadata=text)
    except (UnicodeDecodeError, ssl.SSLError, ValueError) as exc:
        logger.warning(
            "бандл сертификатов НУЦ не разобран как PEM",
            extra={"ca_bundle_path": path, "error_type": type(exc).__name__},
        )
        return "invalid"
    return "loaded"


def build_tls(settings: Settings) -> TlsSetup:
    if not settings.gigachat_verify_ssl:
        logger.warning(
            "проверка TLS до GigaChat ВЫКЛЮЧЕНА (GIGACHAT_VERIFY_SSL=false): только dev"
        )
        return TlsSetup(verify=False, ca_bundle="not_set")
    context = ssl.create_default_context(cafile=certifi.where())
    status = load_ca_bundle(context, settings.gigachat_ca_bundle)
    return TlsSetup(verify=context, ca_bundle=status)


def build_http_client(
    settings: Settings,
    tls: TlsSetup,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """HTTP-клиент до GigaChat: TLS НУЦ, тайм-ауты, необязательный прокси.

    `transport` — для тестов (httpx.MockTransport): с ним verify и proxy не применяются.
    """
    timeout = httpx.Timeout(
        settings.llm_timeout_seconds, connect=settings.llm_connect_timeout_seconds
    )
    if transport is not None:
        return httpx.AsyncClient(timeout=timeout, transport=transport)
    return httpx.AsyncClient(
        timeout=timeout,
        verify=tls.verify,
        proxy=settings.gigachat_proxy_url or None,
    )
