"""The workflow: conversation in, tagged + assigned + internal-note draft out; send only after a person approves."""

from __future__ import annotations

import hashlib
import html
import json
import logging
from dataclasses import dataclass

from . import guard
from .ai import AIError, Triage
from .config import Settings
from .intercom_api import IntercomClient, IntercomError
from .kb import KnowledgeBase
from .store import Store

log = logging.getLogger("intercom_assistant")

CUSTOMER_AUTHORS = {"user", "lead", "contact"}
CUSTOMER_PART_TYPES = {"comment", "open", "conversation"}
MARKER_PREFIX = "ai-draft-ref:"


def log_event(event: str, **fields) -> None:
    """One structured JSON line per step. Values pass through guard.redact: no personal data in logs."""
    safe = {k: (guard.redact(v) if isinstance(v, str) else v) for k, v in fields.items()}
    log.info(json.dumps({"event": event, **safe}, sort_keys=True))


@dataclass
class Result:
    outcome: str
    conversation_id: str
    draft_id: int | None = None
    detail: str = ""


def marker_for(message_key: str) -> str:
    return MARKER_PREFIX + hashlib.sha256(message_key.encode()).hexdigest()[:16]


def latest_customer_message(conv: dict) -> dict | None:
    """The newest message written by the customer: a conversation part, else the opening message."""
    parts = (conv.get("conversation_parts") or {}).get("conversation_parts") or []
    for part in reversed(parts):
        author = part.get("author") or {}
        if author.get("type") in CUSTOMER_AUTHORS and part.get("part_type") in CUSTOMER_PART_TYPES \
                and guard.strip_html(part.get("body") or ""):
            return {"id": f"part-{part['id']}", "text": guard.strip_html(part["body"]),
                    "author_id": str(author.get("id", ""))}
    source = conv.get("source") or {}
    author = source.get("author") or {}
    if author.get("type") in CUSTOMER_AUTHORS and guard.strip_html(source.get("body") or ""):
        return {"id": f"source-{source.get('id') or conv.get('id')}", "text": guard.strip_html(source["body"]),
                "author_id": str(author.get("id", ""))}
    return None


def transcript_of(conv: dict, limit_chars: int) -> str:
    """Customer and teammate messages, oldest first. Internal notes are left out."""
    lines = []
    source = conv.get("source") or {}
    entries = [(source.get("author") or {}, source.get("body") or "", "comment")]
    for part in (conv.get("conversation_parts") or {}).get("conversation_parts") or []:
        entries.append((part.get("author") or {}, part.get("body") or "", part.get("part_type") or ""))
    for author, body, part_type in entries:
        text = guard.strip_html(body)
        if not text or part_type not in CUSTOMER_PART_TYPES:
            continue
        if author.get("type") in CUSTOMER_AUTHORS:
            lines.append("Customer: " + guard.fence(text, limit_chars))
        elif author.get("type") in {"admin", "bot"}:
            lines.append(f"{'Teammate' if author.get('type') == 'admin' else 'Bot'}: {text[:limit_chars]}")
    return "\n".join(lines[-12:])


def note_has_marker(conv: dict, marker: str) -> str | None:
    for part in (conv.get("conversation_parts") or {}).get("conversation_parts") or []:
        if part.get("part_type") == "note" and marker in (part.get("body") or ""):
            return str(part.get("id"))
    return None


class Assistant:
    def __init__(self, settings: Settings, api: IntercomClient, triager, kb: KnowledgeBase, store: Store,
                 heartbeat=None):
        self.s = settings
        self.api = api
        self.ai = triager
        self.kb = kb
        self.store = store
        self.hb = heartbeat
        self._admin_id = settings.admin_id or None

    # --- helpers ----------------------------------------------------------
    @property
    def admin_id(self) -> str:
        if not self._admin_id:
            self._admin_id = self.api.me().id
        return self._admin_id

    def customer_reply_text(self, draft: str) -> str:
        disclosure = self.s.disclosure()
        return f"{disclosure}\n\n{draft}" if disclosure else draft

    def _tag(self, conversation_id: str, name: str) -> None:
        name = name[:191]
        tag_id = self.store.tag_id(name)
        if not tag_id:
            tag_id = str(self.api.create_tag(name)["id"])  # POST /tags creates or returns the existing tag
            self.store.save_tag(name, tag_id)
        self.api.tag_conversation(conversation_id, tag_id, self.admin_id)

    def _alert(self, kind: str, reason: str) -> None:
        if self.hb is not None:
            self.hb.alert(kind, guard.redact(reason))

    def _note_html(self, draft_id: int, t: Triage, reply_text: str, marker: str, titles: dict) -> str:
        e = html.escape
        flags = []
        if t.injection_suspected:
            flags.append("Possible prompt injection in the customer's message: the AI was told to ignore it. "
                         "Check the message before sending anything.")
        if t.needs_human:
            flags.append("Needs a person: check the facts or write the reply yourself.")
        flags += [f"Check: {p}" for p in t.problems]
        sources = "".join(f"<li>{e(c)}: {e(titles.get(c, c))}</li>" for c in t.citations) or "<li>none</li>"
        return (
            f"<p><b>AI draft #{draft_id} (not sent to the customer)</b></p>"
            f"<p>Intent: {e(t.intent)} | Priority: {e(t.priority)} | Sentiment: {e(t.sentiment)}"
            f" | Language: {e(t.language)}</p>"
            f"<p>Summary: {e(t.summary)}</p>"
            + "".join(f"<p>⚠️ {e(f)}</p>" for f in flags)
            + "<p><b>Suggested reply:</b></p>" + guard.to_html(reply_text)
            + f"<p><b>Sources:</b></p><ul>{sources}</ul>"
            f"<p>To send it unchanged: <code>intercom-assistant approve {draft_id}</code> "
            f"(or reject: <code>intercom-assistant reject {draft_id}</code>).</p>"
            f"<p><i>{marker}</i></p>"
        )

    # --- the main step --------------------------------------------------------
    def handle_conversation(self, conversation_id: str, trigger: str = "webhook") -> Result:
        if self.s.paused:
            log_event("skipped_paused", conversation_id=conversation_id)
            return Result("paused", conversation_id)
        conv = self.api.get_conversation(conversation_id)
        msg = latest_customer_message(conv)
        if not msg:
            return Result("no_customer_message", conversation_id)
        key = f"{conversation_id}:{msg['id']}"
        if not self.store.claim(key, conversation_id):
            log_event("duplicate", conversation_id=conversation_id, trigger=trigger)
            return Result("duplicate", conversation_id, detail=self.store.outcome(key) or "")
        try:
            result = self._process(conv, conversation_id, msg, key)
        except Exception as exc:
            self.store.release(key)
            log_event("failed", conversation_id=conversation_id, error=f"{type(exc).__name__}: {exc}")
            self._alert("failure", f"conversation {conversation_id}: {type(exc).__name__}: {exc}")
            raise
        self.store.finish(key, result.outcome)
        if self.hb is not None:
            self.hb.record(1)
        return result

    def _process(self, conv: dict, conversation_id: str, msg: dict, key: str) -> Result:
        contact = msg["author_id"]
        if contact and self.store.is_suppressed(contact):
            log_event("suppressed", conversation_id=conversation_id)
            return Result("suppressed", conversation_id)
        if guard.is_opt_out(msg["text"]):
            if contact:
                self.store.suppress(contact, "customer asked for no AI")
            self._tag(conversation_id, f"{self.s.tag_prefix}: opted out")
            self.api.add_note(conversation_id, self.admin_id,
                              "<p><b>AI assistant:</b> the customer asked not to get AI-written replies, so no "
                              "draft was made and none will be for this contact. Please reply yourself.</p>"
                              f"<p><i>{marker_for(key)}</i></p>")
            log_event("opted_out", conversation_id=conversation_id)
            return Result("opted_out", conversation_id)
        if self.store.drafts_today() >= self.s.max_drafts_per_day:
            self._alert("failure", f"daily draft cap of {self.s.max_drafts_per_day} reached")
            return Result("cap_reached", conversation_id)

        transcript = transcript_of(conv, self.s.max_customer_chars)
        injection_by_code = guard.looks_like_injection(msg["text"])
        hits = self.kb.search(msg["text"], self.s.kb_top_k)
        sections = [(s.id, s.title, s.text) for s, _ in hits]
        try:
            triage = self.ai.triage(transcript, sections)
        except AIError as exc:
            self._tag(conversation_id, f"{self.s.tag_prefix}: needs human")
            self.api.add_note(conversation_id, self.admin_id,
                              f"<p><b>AI assistant:</b> no draft for this message ({html.escape(str(exc))}). "
                              f"Please reply yourself.</p><p><i>{marker_for(key)}</i></p>")
            log_event("ai_declined", conversation_id=conversation_id, reason=str(exc))
            return Result("ai_failed", conversation_id, detail=str(exc))
        if injection_by_code and not triage.injection_suspected:
            triage.injection_suspected = True
            triage.needs_human = True
            triage.problems.append("injection pattern found by the code check")

        tags = [f"{self.s.tag_prefix}: {triage.intent}", f"{self.s.tag_prefix}: {triage.priority}"]
        if triage.needs_human:
            tags.append(f"{self.s.tag_prefix}: needs human")
        if triage.injection_suspected:
            tags.append(f"{self.s.tag_prefix}: injection suspected")
        for name in tags:
            self._tag(conversation_id, name)

        assignee = (self.s.routing or {}).get(triage.intent)
        unassigned = not conv.get("admin_assignee_id") and not conv.get("team_assignee_id")
        if assignee and unassigned:
            self.api.assign(conversation_id, self.admin_id, str(assignee), self.s.routing_type)

        reply_text = self.customer_reply_text(triage.draft_reply)
        marker = marker_for(key)
        meta = {"contact_id": msg["author_id"], "intent": triage.intent, "priority": triage.priority,
                "sentiment": triage.sentiment, "citations": triage.citations, "needs_human": triage.needs_human,
                "injection_suspected": triage.injection_suspected, "model": self.s.model,
                "tokens": [triage.input_tokens, triage.output_tokens]}
        draft_id = self.store.add_draft(key, conversation_id, reply_text, meta, None)
        existing = note_has_marker(conv, marker)
        try:
            if existing:
                note_part = existing
            else:
                titles = {s.id: s.title for s, _ in hits}
                note = self.api.add_note(conversation_id, self.admin_id,
                                         self._note_html(draft_id, triage, reply_text, marker, titles))
                note_part = _last_part_id(note)
        except Exception:
            self.store.db.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
            raise
        self.store.db.execute("UPDATE drafts SET note_part_id = ? WHERE id = ?", (note_part, draft_id))
        log_event("drafted", conversation_id=conversation_id, draft_id=draft_id, intent=triage.intent,
                  priority=triage.priority, needs_human=triage.needs_human,
                  injection=triage.injection_suspected, citations=len(triage.citations))
        return Result("drafted", conversation_id, draft_id)

    # --- the human step -----------------------------------------------------
    def approve(self, draft_id: int, approver: str) -> Result:
        if self.s.paused:
            return Result("paused", "", draft_id)
        draft = self.store.get_draft(draft_id)
        if not draft:
            return Result("not_found", "", draft_id)
        if draft.status != "pending":
            return Result(f"already_{draft.status}", draft.conversation_id, draft_id)
        if draft.meta.get("contact_id") and self.store.is_suppressed(draft.meta["contact_id"]):
            self.store.transition(draft_id, "pending", "rejected", "opt-out")
            return Result("suppressed", draft.conversation_id, draft_id)
        if not self.store.transition(draft_id, "pending", "approved", approver):
            return Result("already_decided", draft.conversation_id, draft_id)
        try:
            resp = self.api.reply(draft.conversation_id, self.admin_id, guard.to_html(draft.body))
        except IntercomError as exc:
            self.store.transition(draft_id, "approved", "failed")
            self._alert("failure", f"sending draft {draft_id} failed: {exc}")
            log_event("send_failed", draft_id=draft_id, error=str(exc))
            return Result("send_failed", draft.conversation_id, draft_id, str(exc))
        self.store.set_reply_part(draft_id, _last_part_id(resp) or "")
        self.store.transition(draft_id, "approved", "sent")
        log_event("sent", draft_id=draft_id, conversation_id=draft.conversation_id)
        return Result("sent", draft.conversation_id, draft_id)

    def reject(self, draft_id: int, approver: str) -> Result:
        draft = self.store.get_draft(draft_id)
        if not draft:
            return Result("not_found", "", draft_id)
        if not self.store.transition(draft_id, "pending", "rejected", approver):
            return Result(f"already_{draft.status}", draft.conversation_id, draft_id)
        log_event("rejected", draft_id=draft_id)
        return Result("rejected", draft.conversation_id, draft_id)

    def retry_failed(self, draft_id: int) -> bool:
        """A send that failed goes back to pending (check the conversation first: it may have landed)."""
        return self.store.transition(draft_id, "failed", "pending")


def _last_part_id(conv: dict | None) -> str | None:
    parts = ((conv or {}).get("conversation_parts") or {}).get("conversation_parts") or []
    return str(parts[-1]["id"]) if parts else None
