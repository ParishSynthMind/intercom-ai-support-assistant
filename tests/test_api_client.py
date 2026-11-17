import httpx
import pytest

from intercom_assistant.intercom_api import IntercomClient, IntercomError, RateLimited, _wait_seconds


def test_me(api):
    assert api.me().id == "991"


def test_sends_version_and_auth_headers(fake, api):
    api.me()
    assert fake.requests[-1][:2] == ("GET", "/me")


def test_wrong_token_is_401(fake):
    bad = IntercomClient("wrong", "https://api.intercom.io", transport=fake.transport(), sleep=lambda s: None)
    with pytest.raises(IntercomError) as e:
        bad.me()
    assert e.value.status == 401 and e.value.code == "unauthorized"


def test_wrong_version_rejected(fake):
    c = IntercomClient("test-token", "https://api.intercom.io", api_version="2.11", transport=fake.transport())
    with pytest.raises(IntercomError) as e:
        c.me()
    assert e.value.code == "intercom_version_invalid"


def test_empty_token_refused():
    with pytest.raises(ValueError):
        IntercomClient("", "https://api.intercom.io")


def test_get_conversation_plaintext(fake, api, conv):
    cid, _ = conv()
    api.get_conversation(cid)
    req = [r for r in fake.requests if r[1] == f"/conversations/{cid}"]
    assert req


def test_get_conversation_sends_display_as():
    seen = {}

    def handler(request):
        seen["q"] = dict(request.url.params)
        return httpx.Response(200, json={"id": "1"})
    c = IntercomClient("t", "https://api.intercom.io", transport=httpx.MockTransport(handler))
    c.get_conversation("1")
    assert seen["q"] == {"display_as": "plaintext"}


def test_missing_conversation_404(api):
    with pytest.raises(IntercomError) as e:
        api.get_conversation("nope")
    assert e.value.status == 404


def test_429_retried_then_ok(fake):
    waits = []
    c = IntercomClient("test-token", "https://api.intercom.io", transport=fake.transport(), sleep=waits.append)
    fake.fail_next = [(429, {"Retry-After": "3"}), (429, {"Retry-After": "1"})]
    assert c.me().id == "991"
    assert waits == [3.0, 1.0]


def test_429_gives_up_after_max_retries(fake):
    c = IntercomClient("test-token", "https://api.intercom.io", transport=fake.transport(), sleep=lambda s: None,
                       max_retries=2)
    fake.fail_next = [(429, {})] * 5
    with pytest.raises(RateLimited):
        c.me()


def test_write_retried_on_429(fake, api):
    fake.fail_next = [(429, {})]
    assert api.create_tag("AI: billing")["name"] == "AI: billing"


def test_read_retried_on_500(fake, api):
    fake.fail_next = [(503, {})]
    assert api.me().id == "991"


def test_write_not_retried_on_500(fake, api, conv):
    cid, _ = conv()
    fake.fail_next = [(500, {})]
    with pytest.raises(IntercomError) as e:
        api.add_note(cid, "991", "<p>x</p>")
    assert e.value.status == 500
    assert fake.notes(cid) == []


def test_connect_error_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"id": "1", "name": "a"})
    c = IntercomClient("t", "https://x", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert c.me().id == "1" and calls["n"] == 2


def test_write_timeout_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("slow")
    c = IntercomClient("t", "https://x", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    with pytest.raises(IntercomError):
        c.add_note("1", "2", "<p>x</p>")
    assert calls["n"] == 1


def test_read_timeout_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json={"id": "1"})
    c = IntercomClient("t", "https://x", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert c.get_conversation("1")["id"] == "1"


def test_wait_seconds_reset_header():
    r = httpx.Response(429, headers={"X-RateLimit-Reset": "1010"})
    assert _wait_seconds(r, 0, now=1000.0) == 10.0


def test_wait_seconds_capped():
    r = httpx.Response(429, headers={"Retry-After": "9999"})
    assert _wait_seconds(r, 0, now=0) == 60


def test_wait_seconds_backoff_default():
    assert _wait_seconds(httpx.Response(429), 3, now=0) == 8.0


def test_create_tag_idempotent(api):
    a = api.create_tag("AI: bug")
    b = api.create_tag("ai: BUG")
    assert a["id"] == b["id"]


def test_tag_conversation(fake, api, conv):
    cid, _ = conv()
    t = api.create_tag("AI: how_to")
    api.tag_conversation(cid, t["id"], "991")
    assert fake.tag_names(cid) == ["AI: how_to"]


def test_note_is_internal(fake, api, conv):
    cid, _ = conv()
    api.add_note(cid, "991", "<p>draft</p>")
    assert len(fake.notes(cid)) == 1 and fake.replies(cid) == []
    assert fake.requests[-1][2]["message_type"] == "note"


def test_reply_is_comment(fake, api, conv):
    cid, _ = conv()
    api.reply(cid, "991", "<p>hello</p>")
    assert len(fake.replies(cid)) == 1
    assert fake.requests[-1][2]["message_type"] == "comment"


def test_assign_team(fake, api, conv):
    cid, _ = conv()
    api.assign(cid, "991", "555", "team")
    assert fake.conversations[cid]["team_assignee_id"] == "555"
    assert fake.requests[-1][2]["skip_notifications"] is True


def test_assign_admin(fake, api, conv):
    cid, _ = conv()
    api.assign(cid, "991", "777", "admin")
    assert fake.conversations[cid]["admin_assignee_id"] == "777"


def test_assign_bad_type(api):
    with pytest.raises(ValueError):
        api.assign("1", "2", "3", "robot")


def test_create_contact_and_conversation(fake, api):
    c = api.create_contact("new@example.test", "New")
    r = api.create_conversation(c["id"], "Hello there")
    assert r["conversation_id"] in fake.conversations


def test_create_contact_conflict(api):
    api.create_contact("dup@example.test")
    with pytest.raises(IntercomError) as e:
        api.create_contact("dup@example.test")
    assert e.value.status == 409


def test_search_conversations(api, conv):
    cid, _ = conv()
    assert cid in [c["id"] for c in api.search_conversations(0)]


def test_error_message_parsed(api):
    with pytest.raises(IntercomError, match="conversation_not_found"):
        api.get_conversation("missing")


def test_calls_logged(api):
    api.me()
    assert api.calls[-1] == ("GET", "/me", 200)
