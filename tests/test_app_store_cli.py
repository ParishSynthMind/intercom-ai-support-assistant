import json

import pytest
from fastapi.testclient import TestClient

from intercom_assistant import cli
from intercom_assistant.app import create_app
from intercom_assistant.signature import sign
from intercom_assistant.store import Store

from .conftest import SECRET

APPROVER = "approver-test-token"


@pytest.fixture
def client(assistant):
    return TestClient(create_app(assistant, SECRET, APPROVER))


def post_event(client, fake, topic, cid, notif_id=None, secret=SECRET):
    body, headers = fake.notification(topic, cid, secret, notif_id)
    return client.post("/webhooks/intercom", content=body, headers=headers)


def test_head_validation(client):
    assert client.head("/webhooks/intercom").status_code == 200


def test_webhook_end_to_end(client, fake, conv):
    cid, _ = conv()
    r = post_event(client, fake, "conversation.user.created", cid)
    assert r.status_code == 200 and r.json()["queued"] == cid
    assert len(fake.notes(cid)) == 1  # background task ran


def test_webhook_bad_signature(client, fake, conv):
    cid, _ = conv()
    r = post_event(client, fake, "conversation.user.created", cid, secret="wrong")
    assert r.status_code == 401 and fake.notes(cid) == []


def test_webhook_unsigned(client, fake, conv):
    cid, _ = conv()
    body, _ = fake.notification("conversation.user.created", cid, SECRET)
    assert client.post("/webhooks/intercom", content=body).status_code == 401


def test_webhook_duplicate_delivery(client, fake, conv):
    cid, _ = conv()
    post_event(client, fake, "conversation.user.created", cid, "notif_same")
    r = post_event(client, fake, "conversation.user.created", cid, "notif_same")
    assert r.json().get("duplicate") and len(fake.notes(cid)) == 1


def test_two_topics_same_message_one_draft(client, fake, conv):
    cid, _ = conv()
    post_event(client, fake, "conversation.user.created", cid)
    post_event(client, fake, "conversation.user.replied", cid)
    assert len(fake.notes(cid)) == 1


def test_webhook_ignores_other_topics(client, fake, conv):
    cid, _ = conv()
    r = post_event(client, fake, "conversation.admin.noted", cid)
    assert r.json()["ignored"] == "conversation.admin.noted" and fake.notes(cid) == []


def test_webhook_ping(client, fake):
    body = json.dumps({"type": "notification_event", "topic": "ping", "id": "n1", "data": {"item": {}}}).encode()
    r = client.post("/webhooks/intercom", content=body, headers={"X-Hub-Signature": sign(body, SECRET)})
    assert r.json()["ping"]


def test_webhook_not_json(client):
    body = b"not json"
    r = client.post("/webhooks/intercom", content=body, headers={"X-Hub-Signature": sign(body, SECRET)})
    assert r.status_code == 400


def test_webhook_wrong_type(client):
    body = json.dumps({"type": "something"}).encode()
    r = client.post("/webhooks/intercom", content=body, headers={"X-Hub-Signature": sign(body, SECRET)})
    assert r.status_code == 400


def test_webhook_missing_ids(client):
    body = json.dumps({"type": "notification_event", "topic": "conversation.user.created", "data": {}}).encode()
    r = client.post("/webhooks/intercom", content=body, headers={"X-Hub-Signature": sign(body, SECRET)})
    assert r.status_code == 400


def test_webhook_too_large(client):
    body = b"x" * 1_000_001
    r = client.post("/webhooks/intercom", content=body, headers={"X-Hub-Signature": sign(body, SECRET)})
    assert r.status_code == 413


def test_webhook_processing_error_still_200(client, fake, conv):
    cid, _ = conv()
    fake.fail_paths["/reply"] = 500
    r = post_event(client, fake, "conversation.user.created", cid)
    assert r.status_code == 200


def test_drafts_needs_token(client):
    assert client.get("/drafts").status_code == 401
    assert client.get("/drafts", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_drafts_list_and_approve(client, fake, conv):
    cid, _ = conv()
    post_event(client, fake, "conversation.user.created", cid)
    auth = {"Authorization": f"Bearer {APPROVER}", "X-Approver": "sam"}
    drafts = client.get("/drafts", headers=auth).json()
    assert len(drafts) == 1 and drafts[0]["conversation_id"] == cid
    r = client.post(f"/drafts/{drafts[0]['id']}/approve", headers=auth)
    assert r.status_code == 200 and len(fake.replies(cid)) == 1
    assert client.post(f"/drafts/{drafts[0]['id']}/approve", headers=auth).status_code == 409


def test_reject_endpoint(client, fake, conv):
    cid, _ = conv()
    post_event(client, fake, "conversation.user.created", cid)
    auth = {"Authorization": f"Bearer {APPROVER}"}
    d = client.get("/drafts", headers=auth).json()[0]["id"]
    assert client.post(f"/drafts/{d}/reject", headers=auth).status_code == 200
    assert client.post(f"/drafts/{d}/reject", headers=auth).status_code == 409
    assert client.post("/drafts/999/reject", headers=auth).status_code == 404


def test_approve_unknown_404(client):
    r = client.post("/drafts/999/approve", headers={"Authorization": f"Bearer {APPROVER}"})
    assert r.status_code == 404


def test_approvals_off_without_token(assistant):
    c = TestClient(create_app(assistant, SECRET, None))
    assert c.get("/drafts", headers={"Authorization": "Bearer x"}).status_code == 503


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True, "paused": False, "pending_drafts": 0}


# --- store ------------------------------------------------------------------
def test_store_first_delivery():
    s = Store()
    assert s.first_delivery("n1", "t") and not s.first_delivery("n1", "t")


def test_store_claim_release():
    s = Store()
    assert s.claim("k", "c") and not s.claim("k", "c")
    s.release("k")
    assert s.claim("k", "c")


def test_store_release_keeps_finished():
    s = Store()
    s.claim("k", "c")
    s.finish("k", "drafted")
    s.release("k")
    assert s.outcome("k") == "drafted"


def test_store_transition_cas():
    s = Store()
    d = s.add_draft("k", "c", "body", {}, None)
    assert s.transition(d, "pending", "approved", "a")
    assert not s.transition(d, "pending", "approved", "b")
    assert s.get_draft(d).decided_by == "a"


def test_store_drafts_today_uses_clock():
    now = [1_700_000_000]
    s = Store(clock=lambda: now[0])
    s.add_draft("k1", "c", "b", {}, None)
    assert s.drafts_today() == 1
    now[0] += 86400
    assert s.drafts_today() == 0


def test_store_suppression():
    s = Store()
    s.suppress("c1", "stop")
    assert s.is_suppressed("c1")
    s.unsuppress("c1")
    assert not s.is_suppressed("c1")


def test_store_file_persists(tmp_path):
    p = tmp_path / "sub" / "a.db"
    s = Store(p)
    s.save_tag("AI: Bug", "9")
    assert Store(p).tag_id("ai: bug") == "9"


def test_store_list_all():
    s = Store()
    a = s.add_draft("k1", "c", "b", {}, None)
    s.add_draft("k2", "c", "b", {}, None)
    s.transition(a, "pending", "rejected")
    assert len(s.list_drafts()) == 1 and len(s.list_drafts(None)) == 2


# --- cli ----------------------------------------------------------------------
def test_cli_check_heartbeat_missed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HEARTBEAT_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("HEARTBEAT_PING_URL", raising=False)
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("ALERT_EMAIL_TO", raising=False)
    assert cli.main(["check-heartbeat", "--max-age-hours", "1"]) == 1
    assert "missed" in capsys.readouterr().out


def test_cli_process_and_approve(tmp_path, monkeypatch, fake, capsys):
    monkeypatch.setenv("ASSISTANT_DB", str(tmp_path / "a.db"))
    monkeypatch.setenv("HEARTBEAT_STATE_DIR", str(tmp_path / "hb"))
    c = fake.add_contact("cli@example.test")
    cid = fake.start_conversation(c["id"], "How do I cancel my plan?")["id"]
    from intercom_assistant.intercom_api import IntercomClient
    real_init = IntercomClient.__init__

    def init(self, token, base_url, *a, **k):
        k["transport"] = fake.transport()
        real_init(self, "test-token", base_url, *a, **k)
    monkeypatch.setattr(IntercomClient, "__init__", init)
    monkeypatch.setattr(cli, "Secrets", lambda: type("S", (), {"get": lambda self, n, required=True: "x"})())
    real_build = cli.build
    monkeypatch.setattr(cli, "build", lambda args: real_build(args, fake_ai=True))
    assert cli.main(["process", cid]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "drafted"
    assert cli.main(["drafts"]) == 0
    assert "conversation" in capsys.readouterr().out
    assert cli.main(["approve", "1", "--by", "tester"]) == 0
    assert len(fake.replies(cid)) == 1
    assert cli.main(["approve", "1"]) == 1
    assert cli.main(["reject", "1"]) == 1
    assert cli.main(["retry", "1"]) == 1
    capsys.readouterr()
    assert cli.main(["poll", "--minutes", "5"]) == 0
    assert "duplicate" in capsys.readouterr().out
