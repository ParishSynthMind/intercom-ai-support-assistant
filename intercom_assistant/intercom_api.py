"""A small Intercom REST client (API version pinned in config.INTERCOM_API_VERSION).

Only the calls this assistant needs. Every request sends the pinned
`Intercom-Version` header. Rate limits: a 429 is retried after the time in
`Retry-After` or `X-RateLimit-Reset` (capped). Reads are also retried on 5xx
and network errors; writes are NOT retried on 5xx, because the write may have
landed (the service layer checks the conversation for its own marker instead,
so a rerun never posts twice).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .config import INTERCOM_API_VERSION

MAX_WAIT_SECONDS = 60


class IntercomError(RuntimeError):
    def __init__(self, status: int, message: str, code: str = ""):
        super().__init__(f"Intercom API {status} {code}: {message}".strip())
        self.status = status
        self.code = code


class RateLimited(IntercomError):
    pass


@dataclass
class Admin:
    id: str
    name: str
    email: str


def _error_from(resp: httpx.Response) -> IntercomError:
    code, message = "", resp.text[:300]
    try:
        data = resp.json()
        errors = data.get("errors") or []
        if errors:
            code = str(errors[0].get("code", ""))
            message = str(errors[0].get("message", ""))
    except ValueError:
        pass
    cls = RateLimited if resp.status_code == 429 else IntercomError
    return cls(resp.status_code, message, code)


def _wait_seconds(resp: httpx.Response, attempt: int, now: float) -> float:
    retry_after = resp.headers.get("Retry-After")
    if retry_after and retry_after.strip().isdigit():
        return min(float(retry_after), MAX_WAIT_SECONDS)
    reset = resp.headers.get("X-RateLimit-Reset")
    if reset and reset.strip().isdigit():
        return max(0.0, min(float(reset) - now, MAX_WAIT_SECONDS))
    return min(2.0 ** attempt, MAX_WAIT_SECONDS)


class IntercomClient:
    def __init__(self, token: str, base_url: str, api_version: str = INTERCOM_API_VERSION,
                 timeout: float = 20.0, max_retries: int = 4, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep, clock=time.time):
        if not token:
            raise ValueError("an Intercom access token is required")
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Intercom-Version": api_version,
                "User-Agent": "intercom-ai-support-assistant/0.1",
            },
        )
        self.max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self.calls: list[tuple[str, str, int]] = []  # (method, path, status) for logs and tests

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, *, write: bool = False, **kwargs) -> dict:
        attempt = 0
        while True:
            try:
                resp = self._http.request(method, path, **kwargs)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Nothing reached Intercom, so a retry is safe for reads and writes.
                if attempt >= self.max_retries:
                    raise IntercomError(0, f"network error: {exc.__class__.__name__}") from exc
                self._sleep(min(2.0 ** attempt, MAX_WAIT_SECONDS))
                attempt += 1
                continue
            except httpx.TimeoutException as exc:
                if write or attempt >= self.max_retries:
                    raise IntercomError(0, f"timeout: {exc.__class__.__name__}") from exc
                self._sleep(min(2.0 ** attempt, MAX_WAIT_SECONDS))
                attempt += 1
                continue
            self.calls.append((method, path, resp.status_code))
            if resp.status_code < 400:
                return resp.json() if resp.content else {}
            retryable = resp.status_code == 429 or (resp.status_code >= 500 and not write)
            if retryable and attempt < self.max_retries:
                self._sleep(_wait_seconds(resp, attempt, self._clock()))
                attempt += 1
                continue
            raise _error_from(resp)

    # --- reads -----------------------------------------------------------
    def me(self) -> Admin:
        data = self._request("GET", "/me")
        return Admin(id=str(data["id"]), name=data.get("name", ""), email=data.get("email", ""))

    def get_conversation(self, conversation_id: str) -> dict:
        return self._request("GET", f"/conversations/{conversation_id}", params={"display_as": "plaintext"})

    def list_tags(self) -> list[dict]:
        return self._request("GET", "/tags").get("data", [])

    def search_conversations(self, updated_since: int, per_page: int = 20) -> list[dict]:
        body = {"query": {"field": "updated_at", "operator": ">", "value": int(updated_since)},
                "pagination": {"per_page": per_page}}
        return self._request("POST", "/conversations/search", json=body).get("conversations", [])

    # --- writes ----------------------------------------------------------
    def create_tag(self, name: str) -> dict:
        return self._request("POST", "/tags", json={"name": name}, write=True)

    def tag_conversation(self, conversation_id: str, tag_id: str, admin_id: str) -> dict:
        return self._request("POST", f"/conversations/{conversation_id}/tags",
                             json={"id": tag_id, "admin_id": admin_id}, write=True)

    def assign(self, conversation_id: str, admin_id: str, assignee_id: str, assignee_type: str = "team") -> dict:
        body = {"message_type": "assignment", "type": assignee_type, "admin_id": admin_id,
                "assignee_id": assignee_id, "skip_notifications": True}
        if assignee_type not in ("admin", "team"):
            raise ValueError("assignee_type must be 'admin' or 'team'")
        return self._request("POST", f"/conversations/{conversation_id}/parts", json=body, write=True)

    def add_note(self, conversation_id: str, admin_id: str, body_html: str) -> dict:
        """An internal note: only teammates see it, the customer never does."""
        return self._request("POST", f"/conversations/{conversation_id}/reply",
                             json={"message_type": "note", "type": "admin", "admin_id": admin_id,
                                   "body": body_html}, write=True)

    def reply(self, conversation_id: str, admin_id: str, body_html: str) -> dict:
        """A real reply the customer sees. Only called after a person approved the draft."""
        return self._request("POST", f"/conversations/{conversation_id}/reply",
                             json={"message_type": "comment", "type": "admin", "admin_id": admin_id,
                                   "body": body_html}, write=True)

    def create_contact(self, email: str, name: str = "") -> dict:
        body = {"role": "user", "email": email}
        if name:
            body["name"] = name
        return self._request("POST", "/contacts", json=body, write=True)

    def create_conversation(self, contact_id: str, body: str) -> dict:
        return self._request("POST", "/conversations",
                             json={"from": {"type": "user", "id": contact_id}, "body": body}, write=True)
