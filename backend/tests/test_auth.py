"""Auth service unit tests (OAuth helpers; no external calls)."""

import pytest

from app.core.crypto import decrypt, encrypt, hash_secret
from app.services import oauth


def test_encrypt_roundtrip():
    s = "my-access-token-abc"
    assert decrypt(encrypt(s)) == s
    assert encrypt(s) != s  # non-deterministic due to IV


def test_hash_is_one_way_and_stable():
    a = hash_secret("abc")
    b = hash_secret("abc")
    assert a == b
    assert "abc" not in a


def test_pkce_pair_shape():
    verifier, challenge = oauth.generate_pkce_pair()
    assert verifier and challenge
    assert len(challenge) == 43  # S256 base64url, no padding


def test_build_authorize_url_requires_config(monkeypatch):
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "deriv_oauth_client_id", "")
    monkeypatch.setattr(cfg.settings, "deriv_oauth_client_secret", "")
    with pytest.raises(RuntimeError):
        oauth.build_authorize_url("state", "verifier")


def test_build_authorize_url_contains_params(monkeypatch):
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "deriv_oauth_client_id", "12345")
    monkeypatch.setattr(cfg.settings, "deriv_oauth_client_secret", "secret")
    monkeypatch.setattr(cfg.settings, "deriv_oauth_redirect_uri", "http://localhost/cb")
    verifier, _ = oauth.generate_pkce_pair()
    url = oauth.build_authorize_url("STATE123", verifier)
    assert "response_type=code" in url
    assert "client_id=12345" in url
    assert "state=STATE123" in url
    # S256 challenge is derived (not equal to the verifier), but must be present
    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url
    assert "redirect_uri=" in url