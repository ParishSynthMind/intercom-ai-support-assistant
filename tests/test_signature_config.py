import hashlib
import hmac

import pytest

from intercom_assistant import signature
from intercom_assistant.config import (
    DEFAULT_AI_DISCLOSURE,
    INTERCOM_API_VERSION,
    Secrets,
    Settings,
    load_settings,
)

BODY = b'{"type":"notification_event","id":"notif_1"}'


def test_sign_matches_hmac_sha1():
    expected = hmac.new(b"s3cret", BODY, hashlib.sha1).hexdigest()
    assert signature.sign(BODY, "s3cret") == "sha1=" + expected


def test_verify_accepts_valid():
    assert signature.verify(BODY, signature.sign(BODY, "s3cret"), "s3cret")


def test_verify_accepts_uppercase_hex():
    sig = signature.sign(BODY, "s3cret")
    assert signature.verify(BODY, "sha1=" + sig[5:].upper(), "s3cret")


def test_verify_rejects_wrong_secret():
    assert not signature.verify(BODY, signature.sign(BODY, "other"), "s3cret")


def test_verify_rejects_tampered_body():
    sig = signature.sign(BODY, "s3cret")
    assert not signature.verify(BODY + b" ", sig, "s3cret")


@pytest.mark.parametrize("header", [None, "", "sha256=abc", "sha1=", "sha1=zz" + "0" * 38, "sha1=" + "0" * 39, "abc"])
def test_verify_rejects_malformed(header):
    assert not signature.verify(BODY, header, "s3cret")


def test_verify_rejects_empty_secret():
    assert not signature.verify(BODY, signature.sign(BODY, ""), "")


def test_api_version_pinned():
    assert INTERCOM_API_VERSION == "2.18"
    assert Settings().api_version == "2.18"


@pytest.mark.parametrize("region,url", [("us", "https://api.intercom.io"), ("eu", "https://api.eu.intercom.io"),
                                        ("au", "https://api.au.intercom.io")])
def test_region_urls(region, url):
    assert Settings(region=region).base_url == url


def test_unknown_region_rejected():
    with pytest.raises(ValueError):
        _ = Settings(region="mars").base_url


def test_load_settings_file(tmp_path, monkeypatch):
    for v in ("INTERCOM_REGION", "ASSISTANT_MODEL", "ASSISTANT_PAUSED", "ASSISTANT_DB", "INTERCOM_ADMIN_ID"):
        monkeypatch.delenv(v, raising=False)
    f = tmp_path / "a.yaml"
    f.write_text("business_name: Shop\nregion: eu\nmodel: claude-sonnet-5-5\n")
    s = load_settings(f)
    assert (s.business_name, s.region, s.model) == ("Shop", "eu", "claude-sonnet-5-5")


def test_load_settings_rejects_unknown_keys(tmp_path):
    f = tmp_path / "a.yaml"
    f.write_text("modle: typo\n")
    with pytest.raises(ValueError, match="modle"):
        load_settings(f)


def test_load_settings_rejects_other_api_version(tmp_path):
    f = tmp_path / "a.yaml"
    f.write_text("api_version: '2.11'\n")
    with pytest.raises(ValueError, match="pinned"):
        load_settings(f)


def test_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_PAUSED", "true")
    monkeypatch.setenv("INTERCOM_REGION", "au")
    monkeypatch.setenv("ASSISTANT_MODEL", "claude-sonnet-5-5")
    s = load_settings(tmp_path / "missing.yaml")
    assert s.paused and s.region == "au" and s.model == "claude-sonnet-5-5"


def test_repo_config_loads(monkeypatch):
    for v in ("INTERCOM_REGION", "ASSISTANT_MODEL", "ASSISTANT_PAUSED"):
        monkeypatch.delenv(v, raising=False)
    s = load_settings("config/assistant.yaml")
    assert s.model == "claude-haiku-5-5" and not s.paused


def test_disclosure_default_on():
    s = Settings(business_name="Shop")
    assert s.disclosure() == DEFAULT_AI_DISCLOSURE.replace("{business}", "Shop")
    assert "AI assistant" in s.disclosure()


def test_disclosure_blank_falls_back():
    assert "AI assistant" in Settings(ai_disclosure="   ").disclosure()


def test_disclosure_custom():
    assert Settings(ai_disclosure="Hi, I'm {business}'s AI helper.", business_name="X").disclosure() == "Hi, I'm X's AI helper."


def test_disclosure_removed_only_by_written_request():
    assert Settings(ai_disclosure_removed_by_written_request="2026-11-20, CEO email").disclosure() == ""


def test_secrets_env_first(monkeypatch):
    monkeypatch.setenv("INTERCOM_ACCESS_TOKEN", "from-env")
    assert Secrets(lookup=lambda item: "from-keychain").get("intercom_token") == "from-env"


def test_secrets_keychain_fallback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    seen = []
    s = Secrets(lookup=lambda item: seen.append(item) or "kc")
    assert s.get("anthropic_key") == "kc" and seen == ["anthropic-api-key"]


def test_secrets_missing_raises(monkeypatch):
    monkeypatch.delenv("INTERCOM_CLIENT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="intercom-client-secret"):
        Secrets(lookup=lambda item: None).get("client_secret")


def test_secrets_optional(monkeypatch):
    monkeypatch.delenv("APPROVER_TOKEN", raising=False)
    assert Secrets(lookup=lambda item: None).get("approver_token", required=False) is None


def test_secrets_unknown_name():
    with pytest.raises(KeyError):
        Secrets(lookup=lambda i: None).get("nope")
