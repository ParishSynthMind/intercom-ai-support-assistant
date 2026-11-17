"""Live webhook run: a real Intercom workspace calls our server through a public HTTPS tunnel.

Prerequisites: `intercom-assistant serve --port 8765` running, a tunnel (e.g. `cloudflared tunnel --url
http://127.0.0.1:8765`) whose URL is set as the app's webhook endpoint with topics
conversation.user.created and conversation.user.replied.

This script only plays the customer: it creates a test contact, opens a conversation and later replies
as that contact, all through the REST API. Everything else (signature check, fetch, Claude, tags, the
internal note) is done by the running server, triggered by Intercom's own webhooks.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from intercom_assistant.config import Secrets, load_settings  # noqa: E402
from intercom_assistant.intercom_api import IntercomClient  # noqa: E402


def wait_for_notes(api, cid, n, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        conv = api.get_conversation(cid)
        parts = conv["conversation_parts"]["conversation_parts"]
        notes = [p for p in parts if p.get("part_type") == "note"]
        if len(notes) >= n:
            return conv, round(time.time() - t0, 1)
        time.sleep(4)
    return api.get_conversation(cid), None


def main() -> int:
    s = load_settings(ROOT / "config" / "assistant.yaml")
    api = IntercomClient(Secrets().get("intercom_token"), s.base_url, s.api_version)
    stamp = int(time.time())
    log = []
    contact = api.create_contact(f"intercom-webhook-test-{stamp}@example.com", "Webhook Test Customer")
    created = api.create_conversation(contact["id"], "Hello, I was charged twice for this month. Can you help?")
    cid = str(created.get("conversation_id") or created.get("id"))
    log.append(f"{time.strftime('%H:%M:%S')} customer opened conversation {cid} (REST API, as the contact)")
    conv, secs = wait_for_notes(api, cid, 1)
    log.append(f"{time.strftime('%H:%M:%S')} note 1 present: {secs is not None} (after {secs}s, via webhook conversation.user.created)")
    # The customer replies (as the contact) -> conversation.user.replied webhook.
    api._request("POST", f"/conversations/{cid}/reply", write=True,
                 json={"message_type": "comment", "type": "user", "intercom_user_id": contact["id"],
                       "body": "Also, where can I download my invoices?"})
    log.append(f"{time.strftime('%H:%M:%S')} customer replied in the conversation (REST API, as the contact)")
    conv, secs2 = wait_for_notes(api, cid, 2)
    log.append(f"{time.strftime('%H:%M:%S')} note 2 present: {secs2 is not None} (after {secs2}s, via webhook conversation.user.replied)")
    parts = conv["conversation_parts"]["conversation_parts"]
    out = ROOT / "examples" / "live-run"
    (out / "webhook-run.json").write_text(json.dumps({
        "conversation_id": cid,
        "tags": [t.get("name") for t in (conv.get("tags") or {}).get("tags", [])],
        "notes": [p.get("body") for p in parts if p.get("part_type") == "note"],
        "part_types": [p.get("part_type") for p in parts],
        "log": log}, indent=2, ensure_ascii=False))
    print("\n".join(log))
    return 0 if secs is not None and secs2 is not None else 1


if __name__ == "__main__":
    sys.exit(main())
