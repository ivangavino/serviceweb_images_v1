import hashlib

import pytest

from app.security.hmac_auth import (
    canonical_query,
    canonical_request,
    sign,
    timestamp_is_fresh,
    verify,
)


def test_signature_binds_all_canonical_fields():
    body_hash = hashlib.sha256(b'{"x":1}').hexdigest()
    message = canonical_request(
        "POST", "/api/v1/mildiu/tomas", "", "PROT-001", "key-001", "1000", "a" * 32, body_hash
    )
    signature = sign("s" * 40, message)
    assert verify("s" * 40, message, signature)
    altered = canonical_request(
        "POST", "/api/v1/mildiu/tomas", "", "PROT-001", "key-002", "1000", "a" * 32, body_hash
    )
    assert not verify("s" * 40, altered, signature)


def test_query_is_encoded_and_sorted():
    assert canonical_query("z=2&a=hello%20world&a=1") == "a=1&a=hello%20world&z=2"


def test_timestamp_boundaries():
    assert timestamp_is_fresh("1000", 300, now=1300)
    assert not timestamp_is_fresh("1000", 300, now=1301)
    assert not timestamp_is_fresh("-1", 300, now=0)


@pytest.mark.parametrize("nonce", ["short", "bad\nnonce", "á" * 32])
def test_rejects_invalid_nonce(nonce):
    with pytest.raises(ValueError):
        canonical_request("GET", "/x", "", "PROT-001", "key-001", "1", nonce, "0" * 64)

