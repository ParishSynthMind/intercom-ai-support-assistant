"""Command line: run the server, process one conversation, poll, list/approve/reject drafts, demo."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from .ai import ClaudeTriager, FakeTriager
from .config import Secrets, load_settings
from .heartbeat import Heartbeat
from .intercom_api import IntercomClient
from .kb import KnowledgeBase
from .service import Assistant
from .store import Store


def build(args, secrets: Secrets | None = None, fake_ai: bool = False) -> Assistant:
    s = load_settings(args.config)
    secrets = secrets or Secrets()
    api = IntercomClient(secrets.get("intercom_token"), s.base_url, s.api_version,
                         timeout=s.http_timeout, max_retries=s.max_retries)
    triager = FakeTriager() if fake_ai else ClaudeTriager(s.model, secrets.get("anthropic_key"), s.max_tokens)
    hb = Heartbeat("Intercom AI support assistant", state_dir=os.environ.get("HEARTBEAT_STATE_DIR"))
    return Assistant(s, api, triager, KnowledgeBase.from_dir(s.kb_dir), Store(s.db_path), heartbeat=hb)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="intercom-assistant", description=__doc__)
    p.add_argument("--config", default=None, help="settings file (default config/assistant.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="run the webhook server")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    pr = sub.add_parser("process", help="handle one conversation now (no webhook needed)")
    pr.add_argument("conversation_id")
    po = sub.add_parser("poll", help="handle conversations updated in the last N minutes (fallback for missed webhooks)")
    po.add_argument("--minutes", type=int, default=60)
    dr = sub.add_parser("drafts", help="list drafts")
    dr.add_argument("--status", default="pending")
    ap = sub.add_parser("approve", help="send a draft to the customer")
    ap.add_argument("draft_id", type=int)
    ap.add_argument("--by", default=os.environ.get("USER", "cli"))
    rj = sub.add_parser("reject", help="drop a draft")
    rj.add_argument("draft_id", type=int)
    rj.add_argument("--by", default=os.environ.get("USER", "cli"))
    rt = sub.add_parser("retry", help="put a failed send back to pending")
    rt.add_argument("draft_id", type=int)
    hb = sub.add_parser("check-heartbeat", help="alert if no conversation was handled for N hours")
    hb.add_argument("--max-age-hours", type=float, default=26)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.cmd == "check-heartbeat":
        h = Heartbeat("Intercom AI support assistant", state_dir=os.environ.get("HEARTBEAT_STATE_DIR"))
        missed = h.check_missed(args.max_age_hours * 3600)
        print("missed" if missed else "ok")
        return 1 if missed else 0

    a = build(args)
    if args.cmd == "serve":
        import uvicorn

        from .app import create_app

        secrets = Secrets()
        app = create_app(a, secrets.get("client_secret"), secrets.get("approver_token", required=False))
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
        return 0
    if args.cmd == "process":
        r = a.handle_conversation(args.conversation_id, "cli")
        print(json.dumps(r.__dict__))
        return 0
    if args.cmd == "poll":
        since = int(time.time()) - args.minutes * 60
        out = []
        for conv in a.api.search_conversations(since):
            out.append(a.handle_conversation(str(conv["id"]), "poll").__dict__)
        print(json.dumps(out, indent=2))
        return 0
    if args.cmd == "drafts":
        for d in a.store.list_drafts(None if args.status == "all" else args.status):
            print(f"#{d.id} [{d.status}] conversation {d.conversation_id} "
                  f"{d.meta.get('intent')}/{d.meta.get('priority')}"
                  f"{' NEEDS HUMAN' if d.meta.get('needs_human') else ''}\n{d.body}\n")
        return 0
    if args.cmd == "approve":
        r = a.approve(args.draft_id, args.by)
        print(json.dumps(r.__dict__))
        return 0 if r.outcome == "sent" else 1
    if args.cmd == "reject":
        r = a.reject(args.draft_id, args.by)
        print(json.dumps(r.__dict__))
        return 0 if r.outcome == "rejected" else 1
    if args.cmd == "retry":
        ok = a.retry_failed(args.draft_id)
        print("pending again" if ok else "not a failed draft")
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
