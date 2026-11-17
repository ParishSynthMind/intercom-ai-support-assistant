"""A faithful local fake of the parts of the Intercom REST API this assistant uses.

Plugged into httpx as a MockTransport, so the real IntercomClient code runs
unchanged against it. It checks what the real API checks that matters here
(bearer token, Intercom-Version header, required fields, message types), keeps
state (contacts, conversations, parts, tags, assignment), can inject 429s and
5xx errors, and builds signed webhook notifications in Intercom's format.
"""

from __future__ import annotations

import itertools
import json
import time

import httpx

from .signature import sign

ADMIN = {"type": "admin", "id": "991", "name": "Test Admin", "email": "admin@example.test"}


class FakeIntercom:
    def __init__(self, token: str = "test-token", version: str = "2.18"):
        self.token = token
        self.version = version
        self.ids = itertools.count(1000)
        self.contacts: dict[str, dict] = {}
        self.conversations: dict[str, dict] = {}
        self.tags: dict[str, dict] = {}
        self.requests: list[tuple[str, str, dict]] = []
        self.fail_next: list[tuple[int, dict]] = []  # (status, headers) to return before handling
        self.fail_paths: dict[str, int] = {}  # path substring -> status, every time

    # --- set-up helpers for tests -------------------------------------------
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def add_contact(self, email: str, name: str = "Test Customer") -> dict:
        cid = f"c{next(self.ids)}"
        self.contacts[cid] = {"type": "contact", "id": cid, "role": "user", "email": email, "name": name}
        return self.contacts[cid]

    def start_conversation(self, contact_id: str, body: str) -> dict:
        conv_id = str(next(self.ids))
        self.conversations[conv_id] = {
            "type": "conversation", "id": conv_id, "created_at": int(time.time()), "updated_at": int(time.time()),
            "state": "open", "admin_assignee_id": None, "team_assignee_id": None,
            "source": {"type": "conversation", "id": str(next(self.ids)), "delivered_as": "customer_initiated",
                       "body": f"<p>{body}</p>", "author": {"type": "user", "id": contact_id}},
            "contacts": {"type": "contact.list", "contacts": [{"type": "contact", "id": contact_id}]},
            "tags": {"type": "tag.list", "tags": []},
            "conversation_parts": {"type": "conversation_part.list", "conversation_parts": [], "total_count": 0},
        }
        return self.conversations[conv_id]

    def customer_reply(self, conv_id: str, contact_id: str, body: str) -> dict:
        return self._add_part(conv_id, "comment", f"<p>{body}</p>", {"type": "user", "id": contact_id})

    def notification(self, topic: str, conv_id: str, client_secret: str, notif_id: str | None = None,
                     attempts: int = 1) -> tuple[bytes, dict]:
        payload = {"type": "notification_event", "app_id": "testapp", "id": notif_id or f"notif_{next(self.ids)}",
                   "topic": topic, "delivery_attempts": attempts, "first_sent_at": int(time.time()),
                   "created_at": int(time.time()),
                   "data": {"type": "notification_event_data", "item": self.conversations.get(conv_id, {"id": conv_id})}}
        body = json.dumps(payload).encode()
        return body, {"X-Hub-Signature": sign(body, client_secret), "Content-Type": "application/json"}

    def notes(self, conv_id: str) -> list[dict]:
        return [p for p in self._parts(conv_id) if p["part_type"] == "note"]

    def replies(self, conv_id: str) -> list[dict]:
        return [p for p in self._parts(conv_id) if p["part_type"] in ("comment", "assignment")
                and p["author"]["type"] == "admin" and p.get("body")]

    def tag_names(self, conv_id: str) -> list[str]:
        return [t["name"] for t in self.conversations[conv_id]["tags"]["tags"]]

    # --- internals ---------------------------------------------------------------
    def _parts(self, conv_id):
        return self.conversations[conv_id]["conversation_parts"]["conversation_parts"]

    def _add_part(self, conv_id, part_type, body, author, **extra):
        part = {"type": "conversation_part", "id": str(next(self.ids)), "part_type": part_type, "body": body,
                "created_at": int(time.time()), "author": author, **extra}
        self._parts(conv_id).append(part)
        conv = self.conversations[conv_id]
        conv["conversation_parts"]["total_count"] = len(self._parts(conv_id))
        conv["updated_at"] = int(time.time())
        return part

    @staticmethod
    def _error(status, code, message):
        return httpx.Response(status, json={"type": "error.list", "errors": [{"code": code, "message": message}]})

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.method, path, body))
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return self._error(401, "unauthorized", "Access Token Invalid")
        if request.headers.get("Intercom-Version") != self.version:
            return self._error(400, "intercom_version_invalid", "Requested version does not exist")
        if self.fail_next:
            status, headers = self.fail_next.pop(0)
            resp = self._error(status, "rate_limit_exceeded" if status == 429 else "server_error", "fake failure")
            resp.headers.update(headers)
            return resp
        for fragment, status in self.fail_paths.items():
            if fragment in path:
                return self._error(status, "server_error", "fake failure")

        m = request.method
        seg = path.strip("/").split("/")
        if m == "GET" and path == "/me":
            return httpx.Response(200, json=ADMIN)
        if path == "/tags":
            if m == "GET":
                return httpx.Response(200, json={"type": "list", "data": list(self.tags.values())})
            name = (body.get("name") or "").strip()
            if not name or len(name) > 191:
                return self._error(400, "parameter_invalid", "name invalid")
            for t in self.tags.values():
                if t["name"].lower() == name.lower():
                    return httpx.Response(200, json=t)
            tid = str(next(self.ids))
            self.tags[tid] = {"type": "tag", "id": tid, "name": name}
            return httpx.Response(200, json=self.tags[tid])
        if path == "/contacts" and m == "POST":
            if not body.get("email") and not body.get("external_id") and not body.get("role"):
                return self._error(400, "parameter_invalid", "email, external_id or role required")
            for c in self.contacts.values():
                if c["email"] == body.get("email"):
                    return self._error(409, "conflict", "A contact matching those details already exists")
            return httpx.Response(200, json=self.add_contact(body.get("email", ""), body.get("name", "")))
        if path == "/conversations" and m == "POST":
            frm = body.get("from") or {}
            if frm.get("id") not in self.contacts or not body.get("body"):
                return self._error(404 if frm.get("id") else 400, "not_found", "contact not found")
            conv = self.start_conversation(frm["id"], body["body"])
            return httpx.Response(200, json={"type": "user_message", "id": conv["source"]["id"],
                                             "conversation_id": conv["id"]})
        if path == "/conversations/search" and m == "POST":
            since = ((body.get("query") or {}).get("value")) or 0
            found = [c for c in self.conversations.values() if c["updated_at"] > since]
            return httpx.Response(200, json={"type": "conversation.list", "conversations": found,
                                             "total_count": len(found)})
        if len(seg) >= 2 and seg[0] == "conversations":
            conv = self.conversations.get(seg[1])
            if conv is None:
                return self._error(404, "conversation_not_found", "Conversation not found")
            if len(seg) == 2 and m == "GET":
                return httpx.Response(200, json=conv)
            if len(seg) == 3 and seg[2] == "tags" and m == "POST":
                tag = self.tags.get(str(body.get("id")))
                if not tag or not body.get("admin_id"):
                    return self._error(400, "parameter_invalid", "tag id and admin_id required")
                if tag not in conv["tags"]["tags"]:
                    conv["tags"]["tags"].append(tag)
                return httpx.Response(200, json=tag)
            if len(seg) == 3 and seg[2] == "reply" and m == "POST":
                mt = body.get("message_type")
                if body.get("type") != "admin" or mt not in ("note", "comment") or not body.get("body") \
                        or body.get("admin_id") != ADMIN["id"]:
                    return self._error(400, "parameter_invalid", "bad reply")
                if mt == "comment" and not conv["admin_assignee_id"] and not conv["team_assignee_id"]:
                    # As seen live (API 2.18): replying to an unassigned conversation assigns it to the
                    # replier, and the reply is stored as an "assignment" part carrying the body.
                    conv["admin_assignee_id"] = body["admin_id"]
                    mt = "assignment"
                self._add_part(seg[1], mt, body["body"], {"type": "admin", "id": body["admin_id"]})
                return httpx.Response(200, json=conv)
            if len(seg) == 3 and seg[2] == "parts" and m == "POST":
                if body.get("message_type") != "assignment" or body.get("type") not in ("admin", "team"):
                    return self._error(400, "parameter_invalid", "bad assignment")
                key = "team_assignee_id" if body["type"] == "team" else "admin_assignee_id"
                conv[key] = body.get("assignee_id")
                self._add_part(seg[1], "assignment", None, {"type": "admin", "id": body.get("admin_id")})
                return httpx.Response(200, json=conv)
        return self._error(404, "not_found", f"no fake route for {m} {path}")
