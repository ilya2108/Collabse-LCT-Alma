"""TLS: бандл НУЦ с CRLF загружается; отсутствующий или битый файл не роняет старт."""
from __future__ import annotations

import ssl
from pathlib import Path

import certifi
import pytest

from app.config import Settings
from app.main import create_app
from app.tls import build_tls, load_ca_bundle

REPO_BUNDLE = (
    Path(__file__).resolve().parents[3]
    / "deploy/k8s/base/llm-gateway/certs/russian_trusted_bundle.pem"
)


def _certifi_pems(count: int) -> str:
    text = Path(certifi.where()).read_text(encoding="ascii")
    blocks = []
    position = 0
    for _ in range(count):
        start = text.index("-----BEGIN CERTIFICATE-----", position)
        end = text.index("-----END CERTIFICATE-----", start) + len("-----END CERTIFICATE-----")
        blocks.append(text[start:end])
        position = end
    return "\n".join(blocks) + "\n"


def test_crlf_bundle_is_loaded(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.pem"
    bundle.write_bytes(_certifi_pems(2).replace("\n", "\r\n").encode("ascii"))
    empty = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert load_ca_bundle(empty, str(bundle)) == "loaded"
    assert empty.cert_store_stats()["x509_ca"] == 2


def test_build_tls_states(tmp_path: Path) -> None:
    assert build_tls(Settings(gigachat_ca_bundle="")).ca_bundle == "not_set"
    missing = build_tls(Settings(gigachat_ca_bundle=str(tmp_path / "missing.pem")))
    assert missing.ca_bundle == "missing"
    assert isinstance(missing.verify, ssl.SSLContext)  # certifi остаётся

    garbage = tmp_path / "garbage.pem"
    garbage.write_text("это не сертификат", encoding="utf-8")
    assert build_tls(Settings(gigachat_ca_bundle=str(garbage))).ca_bundle == "invalid"

    assert build_tls(Settings(gigachat_verify_ssl=False)).verify is False


def test_missing_bundle_does_not_break_startup(tmp_path: Path) -> None:
    app = create_app(Settings(gigachat_ca_bundle=str(tmp_path / "missing.pem")))
    assert app.state.tls.ca_bundle == "missing"


@pytest.mark.skipif(not REPO_BUNDLE.exists(), reason="бандл НУЦ в deploy/ не найден")
def test_repo_nuc_bundle_has_root_and_sub_ca() -> None:
    raw = REPO_BUNDLE.read_bytes()
    assert b"\r\n" not in raw, "бандл в репозитории хранится в LF"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    assert load_ca_bundle(context, str(REPO_BUNDLE)) == "loaded"
    subjects = {
        dict(item[0] for item in cert["subject"]).get("commonName")
        for cert in context.get_ca_certs()
    }
    assert {"Russian Trusted Root CA", "Russian Trusted Sub CA"} <= subjects
