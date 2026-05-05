"""Tests for key encoding helpers (no subprocess or network)."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from mobile_computer_use.bridge import (
    b64,
    b64url_to_int,
    b64url_uint,
    load_or_create_private_key,
    public_key_from_jwk,
    public_key_to_jwk,
    sha256_hex,
    unb64,
)


@pytest.mark.parametrize("n", [0, 1, 2**256 - 2])
def test_b64url_uint_roundtrip(n: int) -> None:
    assert b64url_to_int(b64url_uint(n)) == n


def test_sha256_hex_stable() -> None:
    assert sha256_hex("hello") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


def test_b64_roundtrip() -> None:
    raw = b"\x00\xff\x10"
    assert unb64(b64(raw)) == raw


def test_jwk_p256_roundtrip(tmp_path: Path) -> None:
    key = load_or_create_private_key(tmp_path / "k.pem")
    pub = key.public_key()
    jwk = public_key_to_jwk(pub)
    assert jwk["kty"] == "EC"
    assert jwk["crv"] == "P-256"
    again = public_key_from_jwk(jwk)
    assert isinstance(again, ec.EllipticCurvePublicKey)
    n1 = pub.public_numbers()
    n2 = again.public_numbers()
    assert n1.x == n2.x and n1.y == n2.y


def test_public_key_from_jwk_rejects_non_p256() -> None:
    with pytest.raises(ValueError, match="browser key must be an EC P-256 JWK"):
        public_key_from_jwk({"kty": "EC", "crv": "P-521", "x": "qqo", "y": "qqo"})
