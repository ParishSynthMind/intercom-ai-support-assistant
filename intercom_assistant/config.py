"""Settings and secrets.

Settings come from a YAML file (config/assistant.yaml) with environment
variables on top. Secrets (the Intercom access token, the app's client secret
used to sign webhooks, the Anthropic key, the approver token) come from the
environment or the macOS Keychain, never from a file in the repo.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Pinned API version (rule B17): one value, sent as the Intercom-Version header.
# 2.18 was released 2026-11-09 and is the newest stable version.
INTERCOM_API_VERSION = "2.18"

REGION_BASE_URLS = {
    "us": "https://api.intercom.io",
    "eu": "https://api.eu.intercom.io",
    "au": "https://api.au.intercom.io",
}

DEFAULT_AI_DISCLOSURE = "I'm an AI assistant for {business}. A member of our team checked this reply before it was sent."

INTENTS = ["billing", "bug", "how_to", "account", "feature_request", "cancellation", "complaint", "other"]
PRIORITIES = ["urgent", "high", "normal", "low"]
SENTIMENTS = ["negative", "neutral", "positive"]


@dataclass
class Settings:
    business_name: str = "Acme Help Desk"
    region: str = "us"
    api_version: str = INTERCOM_API_VERSION
    model: str = "claude-haiku-5-5"
    max_tokens: int = 900
    kb_dir: str = "knowledge_base"
    kb_top_k: int = 3
    db_path: str = "data/assistant.db"
    # Who the notes are written as. Empty = the admin that owns the token (GET /me).
    admin_id: str = ""
    # intent -> admin or team id to assign to. Empty = leave assignment alone.
    routing: dict = field(default_factory=dict)
    routing_type: str = "team"
    tag_prefix: str = "AI"
    # Topics we act on. Anything else is acknowledged and ignored.
    topics: list = field(default_factory=lambda: ["conversation.user.created", "conversation.user.replied"])
    ai_disclosure: str = ""
    ai_disclosure_removed_by_written_request: str = ""
    # Kill switch: true stops every AI call and every write to Intercom.
    paused: bool = False
    # Safety caps.
    max_customer_chars: int = 4000
    max_drafts_per_day: int = 500
    http_timeout: float = 20.0
    max_retries: int = 4

    @property
    def base_url(self) -> str:
        try:
            return REGION_BASE_URLS[self.region]
        except KeyError:
            raise ValueError(f"unknown region {self.region!r}, use one of {sorted(REGION_BASE_URLS)}") from None

    def disclosure(self) -> str:
        """The AI disclosure line (rule B18). On by default; a blank value falls back to the default."""
        if self.ai_disclosure_removed_by_written_request.strip():
            return ""
        text = self.ai_disclosure.strip() or DEFAULT_AI_DISCLOSURE
        return text.replace("{business}", self.business_name)


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    path = Path(path or os.environ.get("ASSISTANT_CONFIG", "config/assistant.yaml"))
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    known = set(Settings.__dataclass_fields__)
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"unknown settings in {path}: {', '.join(unknown)}")
    s = Settings(**data)
    env = os.environ
    if env.get("INTERCOM_REGION"):
        s.region = env["INTERCOM_REGION"]
    if env.get("ASSISTANT_MODEL"):
        s.model = env["ASSISTANT_MODEL"]
    if env.get("ASSISTANT_DB"):
        s.db_path = env["ASSISTANT_DB"]
    if env.get("INTERCOM_ADMIN_ID"):
        s.admin_id = env["INTERCOM_ADMIN_ID"]
    if env.get("ASSISTANT_PAUSED"):
        s.paused = _bool(env["ASSISTANT_PAUSED"])
    if s.api_version != INTERCOM_API_VERSION:
        raise ValueError(f"api_version is pinned to {INTERCOM_API_VERSION} in code; change it there on purpose")
    _ = s.base_url  # validates the region
    return s


def keychain(item: str, account: str | None = None) -> str | None:
    """Read a generic password from the macOS Keychain. None if missing or not on a Mac."""
    attempts = [["-a", account, "-s", item]] if account else []
    attempts.append(["-s", item])
    for args in attempts:
        try:
            out = subprocess.run(["security", "find-generic-password", *args, "-w"],
                                 capture_output=True, text=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        value = out.stdout.strip()
        if out.returncode == 0 and value:
            return value
    return None


SECRET_SOURCES = {
    # name: (environment variable, Keychain item)
    "intercom_token": ("INTERCOM_ACCESS_TOKEN", "intercom-access-token"),
    "client_secret": ("INTERCOM_CLIENT_SECRET", "intercom-client-secret"),
    "anthropic_key": ("ANTHROPIC_API_KEY", "anthropic-api-key"),
    "approver_token": ("APPROVER_TOKEN", "intercom-assistant-approver-token"),
}


class Secrets:
    """Environment variable first, then the Keychain. Cached in memory only, never written anywhere."""

    def __init__(self, lookup=keychain):
        self._lookup = lookup
        self._cache: dict[str, str | None] = {}

    def get(self, name: str, required: bool = True) -> str | None:
        if name not in SECRET_SOURCES:
            raise KeyError(name)
        if name not in self._cache:
            env_var, item = SECRET_SOURCES[name]
            self._cache[name] = os.environ.get(env_var) or self._lookup(item)
        value = self._cache[name]
        if required and not value:
            env_var, item = SECRET_SOURCES[name]
            raise RuntimeError(f"missing secret: set {env_var} or add Keychain item '{item}'")
        return value
