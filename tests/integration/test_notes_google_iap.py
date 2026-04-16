"""Integration coverage for the Google IAP reference notes example.

The example validates the signed ``x-goog-iap-jwt-assertion`` header via
Google's public JWKS. To keep the tests hermetic, we generate a throwaway
``ES256`` keypair, pre-seed the shared JWKS cache in
:mod:`litestar_mcp.auth` with the public key, and mint tokens locally.
No live Google metadata fetches are performed.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from docs.examples.notes.shared.auth import (
    DEFAULT_IAP_ISSUER,
    IAP_HEADER_NAME,
)
from docs.examples.notes.sqlspec.google_iap import create_app
from jwt.algorithms import ECAlgorithm
from litestar.testing import TestClient

import litestar_mcp.auth._oidc as mcp_auth
from tests.integration.conftest import parse_tool_payload, rpc, rpc_response

TEST_AUDIENCE = "/projects/000000000000/global/backendServices/111111111111"
TEST_JWKS_URL = "https://test.invalid/iap/verify/public_key-jwk"


def _make_keypair(kid: str) -> tuple[ec.EllipticCurvePrivateKey, dict[str, Any]]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(ECAlgorithm(ECAlgorithm.SHA256).to_jwk(private_key.public_key()))
    jwk["kid"] = kid
    jwk["alg"] = "ES256"
    return private_key, jwk


def _mint_iap_token(
    private_key: ec.EllipticCurvePrivateKey,
    *,
    kid: str,
    sub: str,
    email: str | None = "alice@example.com",
    audience: str = TEST_AUDIENCE,
    issuer: str = DEFAULT_IAP_ISSUER,
) -> str:
    claims: dict[str, Any] = {
        "sub": sub,
        "iss": issuer,
        "aud": audience,
    }
    if email is not None:
        claims["email"] = f"accounts.google.com:{email}"
    return jwt.encode(claims, private_key, algorithm="ES256", headers={"kid": kid})


@pytest.fixture
def iap_key() -> Iterator[tuple[ec.EllipticCurvePrivateKey, str]]:
    """Generate an ES256 keypair and seed the shared JWKS cache."""
    kid = f"iap-test-{uuid4().hex[:8]}"
    private_key, jwk = _make_keypair(kid)
    # Pre-seed the litestar_mcp.auth document cache with our JWKS so no
    # real HTTP call is made during validation.
    mcp_auth._JSON_DOCUMENT_CACHE[TEST_JWKS_URL] = (monotonic() + 3600, {"keys": [jwk]})
    yield private_key, kid
    mcp_auth._JSON_DOCUMENT_CACHE.pop(TEST_JWKS_URL, None)


def _make_app(tmp_path: Path) -> Any:
    return create_app(
        database_path=str(tmp_path / "notes-google-iap.sqlite"),
        audience=TEST_AUDIENCE,
        jwks_url=TEST_JWKS_URL,
    )


def test_rejects_missing_iap_header(tmp_path: Path, iap_key: Any) -> None:
    app = _make_app(tmp_path)
    with TestClient(app=app) as client:
        response = rpc_response(
            client,
            "tools/call",
            {"name": "list_notes", "arguments": {}},
        )
        assert response.status_code == 401


def test_rejects_token_signed_by_different_key(tmp_path: Path, iap_key: Any) -> None:
    # Mint a token with a *different* key that the server does not trust.
    rogue_key, _ = _make_keypair("rogue-kid")
    token = _mint_iap_token(rogue_key, kid="rogue-kid", sub="alice")
    app = _make_app(tmp_path)
    with TestClient(app=app) as client:
        response = rpc_response(
            client,
            "tools/call",
            {"name": "list_notes", "arguments": {}},
            headers={IAP_HEADER_NAME: token},
        )
        assert response.status_code == 401


def test_rejects_wrong_audience(tmp_path: Path, iap_key: Any) -> None:
    private_key, kid = iap_key
    token = _mint_iap_token(private_key, kid=kid, sub="alice", audience="wrong-aud")
    app = _make_app(tmp_path)
    with TestClient(app=app) as client:
        response = rpc_response(
            client,
            "tools/call",
            {"name": "list_notes", "arguments": {}},
            headers={IAP_HEADER_NAME: token},
        )
        assert response.status_code == 401


def test_valid_iap_header_scopes_notes_by_sub(tmp_path: Path, iap_key: Any) -> None:
    private_key, kid = iap_key
    alice_token = _mint_iap_token(private_key, kid=kid, sub="alice")
    bob_token = _mint_iap_token(private_key, kid=kid, sub="bob", email="bob@example.com")
    app = _make_app(tmp_path)

    with TestClient(app=app) as client:
        created = rpc(
            client,
            "tools/call",
            {"name": "create_note", "arguments": {"data": {"title": "hi", "body": "world"}}},
            headers={IAP_HEADER_NAME: alice_token},
        )
        note_id = parse_tool_payload(created)["id"]

        listed = rpc(
            client,
            "tools/call",
            {"name": "list_notes", "arguments": {}},
            headers={IAP_HEADER_NAME: alice_token},
        )
        alice_items = parse_tool_payload(listed)
        items = alice_items.get("items") if isinstance(alice_items, dict) else alice_items
        assert isinstance(items, list)
        assert any(item["id"] == note_id for item in items)

        bob_listed = rpc(
            client,
            "tools/call",
            {"name": "list_notes", "arguments": {}},
            headers={IAP_HEADER_NAME: bob_token},
        )
        bob_payload = parse_tool_payload(bob_listed)
        bob_items = bob_payload.get("items") if isinstance(bob_payload, dict) else bob_payload
        assert isinstance(bob_items, list)
        assert not any(item["id"] == note_id for item in bob_items)


def test_well_known_publishes_configured_issuer(tmp_path: Path, iap_key: Any) -> None:
    app = _make_app(tmp_path)
    with TestClient(app=app) as client:
        response = client.get("/.well-known/oauth-protected-resource")
        assert response.status_code == 200
        body = response.json()
        assert (
            body["authorization_servers"] == [DEFAULT_IAP_ISSUER] or DEFAULT_IAP_ISSUER in body["authorization_servers"]
        )
