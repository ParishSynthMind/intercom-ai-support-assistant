"""The webhook endpoint and the small approval API.

- HEAD/POST /webhooks/intercom: Intercom's notifications. The signature is checked
  on the raw body; duplicates (Intercom retries) are dropped by notification id;
  the reply goes back at once and the work runs after it (Intercom wants an
  answer within 5 seconds).
- GET /drafts, POST /drafts/{id}/approve|reject: for a person, behind a bearer
  token (APPROVER_TOKEN). Approving is the ONLY way a customer-facing reply is sent.
- GET /healthz.
"""

from __future__ import annotations

import hmac
import json
import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response

from . import signature
from .service import Assistant, log_event

log = logging.getLogger("intercom_assistant")
MAX_BODY = 1_000_000


def create_app(assistant: Assistant, client_secret: str, approver_token: str | None) -> FastAPI:
    app = FastAPI(title="Intercom AI Support Assistant", docs_url=None, redoc_url=None)

    def process(conversation_id: str) -> None:
        try:
            assistant.handle_conversation(conversation_id, "webhook")
        except Exception as exc:  # already alerted and logged in the service; never crash the server
            log.error("processing %s failed: %s", conversation_id, type(exc).__name__)

    @app.head("/webhooks/intercom")
    def webhook_head() -> Response:
        return Response(status_code=200)

    @app.post("/webhooks/intercom")
    async def webhook(request: Request, background: BackgroundTasks,
                      x_hub_signature: str | None = Header(default=None)):
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(413, "too large")
        if not signature.verify(raw, x_hub_signature, client_secret):
            log_event("bad_signature")
            raise HTTPException(401, "bad signature")
        try:
            event = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "not JSON") from None
        if not isinstance(event, dict) or event.get("type") != "notification_event":
            raise HTTPException(400, "not an Intercom notification")
        topic = event.get("topic", "")
        notif_id = str(event.get("id") or "")
        if topic == "ping":
            return {"ok": True, "ping": True}
        if topic not in assistant.s.topics:
            return {"ok": True, "ignored": topic}
        item = (event.get("data") or {}).get("item") or {}
        conversation_id = str(item.get("id") or "")
        if not notif_id or not conversation_id:
            raise HTTPException(400, "missing notification or conversation id")
        if not assistant.store.first_delivery(notif_id, topic):
            log_event("duplicate_delivery", topic=topic)
            return {"ok": True, "duplicate": True}
        background.add_task(process, conversation_id)
        return {"ok": True, "queued": conversation_id}

    def require_approver(authorization: str | None) -> str:
        if not approver_token:
            raise HTTPException(503, "approvals over HTTP are off: set APPROVER_TOKEN")
        given = (authorization or "").removeprefix("Bearer ").strip()
        if not given or not hmac.compare_digest(given, approver_token):
            raise HTTPException(401, "approver token required")
        return "http-approver"

    @app.get("/drafts")
    def drafts(authorization: str | None = Header(default=None), status: str = "pending"):
        require_approver(authorization)
        return [{"id": d.id, "conversation_id": d.conversation_id, "status": d.status, "reply": d.body,
                 "intent": d.meta.get("intent"), "priority": d.meta.get("priority"),
                 "needs_human": d.meta.get("needs_human"), "citations": d.meta.get("citations")}
                for d in assistant.store.list_drafts(status or None)]

    @app.post("/drafts/{draft_id}/approve")
    def approve(draft_id: int, authorization: str | None = Header(default=None), x_approver: str = Header(default="")):
        who = require_approver(authorization)
        r = assistant.approve(draft_id, (x_approver or who)[:80])
        code = {"sent": 200, "not_found": 404, "send_failed": 502, "paused": 409}.get(r.outcome, 409)
        if code != 200:
            raise HTTPException(code, r.outcome)
        return {"ok": True, "outcome": r.outcome, "conversation_id": r.conversation_id}

    @app.post("/drafts/{draft_id}/reject")
    def reject(draft_id: int, authorization: str | None = Header(default=None), x_approver: str = Header(default="")):
        who = require_approver(authorization)
        r = assistant.reject(draft_id, (x_approver or who)[:80])
        if r.outcome != "rejected":
            raise HTTPException(404 if r.outcome == "not_found" else 409, r.outcome)
        return {"ok": True, "outcome": r.outcome}

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "paused": assistant.s.paused, "pending_drafts": len(assistant.store.list_drafts())}

    return app
