import logging
from pathlib import Path

import pytest

from intercom_assistant.ai import FakeTriager
from intercom_assistant.config import Settings
from intercom_assistant.fake_intercom import FakeIntercom
from intercom_assistant.heartbeat import Heartbeat
from intercom_assistant.intercom_api import IntercomClient
from intercom_assistant.kb import KnowledgeBase
from intercom_assistant.service import Assistant
from intercom_assistant.store import Store

ROOT = Path(__file__).resolve().parent.parent
SECRET = "test-client-secret"


class AlertSink:
    def __init__(self):
        self.alerts = []

    def __call__(self, job, kind, reason, detail=""):
        self.alerts.append((kind, reason))
        return True


@pytest.fixture
def fake():
    return FakeIntercom()


@pytest.fixture
def api(fake):
    return IntercomClient("test-token", "https://api.intercom.io", transport=fake.transport(), sleep=lambda s: None)


@pytest.fixture
def kb():
    return KnowledgeBase.from_dir(ROOT / "knowledge_base")


@pytest.fixture
def settings():
    return Settings(kb_dir=str(ROOT / "knowledge_base"), db_path=":memory:")


@pytest.fixture
def sink():
    return AlertSink()


@pytest.fixture
def hb(tmp_path, sink):
    return Heartbeat("test job", ping_url="", alert=sink, state_dir=str(tmp_path / "hb"), cooldown=0)


@pytest.fixture
def triager():
    return FakeTriager()


@pytest.fixture
def assistant(settings, api, triager, kb, hb):
    return Assistant(settings, api, triager, kb, Store(":memory:"), heartbeat=hb)


@pytest.fixture
def conv(fake):
    """A new conversation from a customer, returns (conversation_id, contact_id)."""
    def make(text="How do I reset my password?", email="cust@example.test"):
        c = fake.add_contact(email)
        conversation = fake.start_conversation(c["id"], text)
        return conversation["id"], c["id"]
    return make


@pytest.fixture(autouse=True)
def _quiet_logs(caplog):
    caplog.set_level(logging.INFO, logger="intercom_assistant")
