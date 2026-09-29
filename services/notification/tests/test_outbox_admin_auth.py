"""Доступ к журналу отправок по Bearer-токену Keycloak с ролью admin (api-contract §9.4)."""
from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI

from app.keycloak import KeycloakJWTVerifier

ISSUER = "http://id.crm.local/realms/crm"
KID = "test-kid"


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture()
def verifier(rsa_key: rsa.RSAPrivateKey) -> KeycloakJWTVerifier:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})

    def jwks_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [jwk]})

    return KeycloakJWTVerifier(
        jwks_endpoint="http://keycloak:8080/realms/crm/protocol/openid-connect/certs",
        allowed_issuers={ISSUER},
        http=httpx.AsyncClient(transport=httpx.MockTransport(jwks_handler)),
    )


def issue_token(rsa_key: rsa.RSAPrivateKey, *, roles: list[str], issuer: str = ISSUER) -> str:
    pem = rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    claims = {
        "iss": issuer,
        "sub": "u-admin",
        "exp": int(time.time()) + 300,
        "realm_access": {"roles": roles},
    }
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": KID})


async def test_verifier_accepts_valid_token(
    verifier: KeycloakJWTVerifier, rsa_key: rsa.RSAPrivateKey
) -> None:
    claims = await verifier.verify(issue_token(rsa_key, roles=["admin", "kam"]))
    assert verifier.realm_roles(claims) == ["admin", "kam"]


async def test_verifier_rejects_foreign_issuer(
    verifier: KeycloakJWTVerifier, rsa_key: rsa.RSAPrivateKey
) -> None:
    from app.keycloak import JWTVerificationError

    with pytest.raises(JWTVerificationError):
        await verifier.verify(issue_token(rsa_key, roles=["admin"], issuer="http://evil"))


async def test_outbox_with_admin_bearer(
    app: FastAPI,
    client: httpx.AsyncClient,
    verifier: KeycloakJWTVerifier,
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    app.state.jwt_verifier = verifier

    admin = await client.get(
        "/api/v1/outbox",
        headers={"Authorization": f"Bearer {issue_token(rsa_key, roles=['admin'])}"},
    )
    assert admin.status_code == 200

    kam = await client.get(
        "/api/v1/outbox",
        headers={"Authorization": f"Bearer {issue_token(rsa_key, roles=['kam'])}"},
    )
    assert kam.status_code == 403
    assert kam.json()["error"]["code"] == "forbidden"
