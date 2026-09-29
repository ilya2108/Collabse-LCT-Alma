"""Валидация JWT Keycloak: подпись по JWKS, iss, exp, roles (без сети — фейковый JWKS)."""

from __future__ import annotations

import json
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import app.core.security as security
from app.core.config import get_settings
from app.core.errors import ApiError

KID = "unit-test-kid"
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _make_token(**overrides: object) -> str:
    settings = get_settings()
    now = int(time.time())
    payload: dict = {
        "sub": str(uuid.uuid4()),
        "iss": settings.keycloak_issuer,
        "aud": "account",
        "exp": now + 300,
        "iat": now,
        "preferred_username": "kam1@demo",
        "email": "kam1@crm.local",
        "name": "Петров Константин Андреевич",
        "realm_access": {"roles": ["kam", "offline_access"]},
        "azp": "crm-frontend",
    }
    payload.update(overrides)
    return jwt.encode(payload, _PRIVATE_KEY, algorithm="RS256", headers={"kid": KID})


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подсовываем JWKS с публичным ключом теста вместо похода в Keycloak."""
    settings = get_settings()
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_PRIVATE_KEY.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    cache = security.JwksCache(settings.jwks_url, ttl_seconds=600)
    cache._keys = {KID: jwt.PyJWK(jwk)}  # noqa: SLF001 — тестовая инъекция
    cache._fetched_at = time.monotonic()  # noqa: SLF001
    monkeypatch.setattr(security, "_jwks_cache", cache)


async def test_valid_token_decodes_claims() -> None:
    claims = await security.decode_token(_make_token())
    assert claims.username == "kam1@demo"
    assert claims.roles == ("kam",)  # прочие realm-роли отфильтрованы
    assert claims.email == "kam1@crm.local"


async def test_expired_token_rejected() -> None:
    token = _make_token(exp=int(time.time()) - 10)
    with pytest.raises(ApiError) as excinfo:
        await security.decode_token(token)
    assert excinfo.value.status_code == 401


async def test_wrong_issuer_rejected() -> None:
    token = _make_token(iss="http://evil.example/realms/crm")
    with pytest.raises(ApiError) as excinfo:
        await security.decode_token(token)
    assert excinfo.value.status_code == 401


async def test_foreign_audience_rejected() -> None:
    token = _make_token(aud="evil-client", azp="evil-client")
    with pytest.raises(ApiError) as excinfo:
        await security.decode_token(token)
    assert excinfo.value.status_code == 401


async def test_unknown_kid_rejected() -> None:
    token = jwt.encode(
        {"sub": "x", "iss": get_settings().keycloak_issuer, "exp": int(time.time()) + 60},
        _PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": "другой-kid"},
    )
    with pytest.raises(ApiError) as excinfo:
        await security.decode_token(token)
    assert excinfo.value.status_code == 401
