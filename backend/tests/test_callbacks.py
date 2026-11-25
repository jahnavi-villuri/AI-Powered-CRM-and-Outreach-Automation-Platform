"""
BE-48 tests: POST /api/v1/callbacks

These tests need NO real database. The database layer (`transaction`) is
replaced with a tiny in-memory fake, so they run anywhere with `pytest`.
"""

import hmac
import hashlib
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from backend.app.api.v1 import callbacks
from backend.app.core.config import get_settings

URL = "/api/v1/callbacks"


# ---------------------------------------------------------------- helpers
def secret() -> str:
    return get_settings().PROVIDER_WEBHOOK_SECRET


def sign(body: bytes, ts: str, key: str = None) -> str:
    key = key or secret()
    return hmac.new(key.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()


def event(**kw):
    d = {
        "provider": "resend",
        "provider_event_id": "evt_1",
        "event_type": "delivered",
        "occurred_at": "2026-10-16T12:00:00Z",
        "provider_message_id": "msg_1",
        "recipient": "hr@acme.example.com",
        "payload": {},
    }
    d.update(kw)
    return d


def post(client, body_dict, *, ts=None, sig=None, send_sig=True, send_ts=True, raw=None):
    body = raw if raw is not None else json.dumps(body_dict).encode()
    ts = ts if ts is not None else str(int(time.time()))
    headers = {"Content-Type": "application/json"}
    if send_sig:
        headers["X-Provider-Signature"] = sig if sig is not None else sign(body, ts)
    if send_ts:
        headers["X-Provider-Timestamp"] = ts
    return client.post(URL, content=body, headers=headers)


# ------------------------------------------------------- in-memory fake DB
class FakeDB:
    def __init__(self):
        self.messages = [
            {"id": "m1", "provider": "resend", "provider_message_id": "msg_1",
             "recipient": "Hr@Acme.example.com", "status": "sent"},
            {"id": "m2", "provider": "resend", "provider_message_id": "msg_2",
             "recipient": "hr@acme.example.com", "status": "queued"},
        ]
        self.events = {}        # provider_event_id -> row
        self.suppressions = {}  # lower(email) -> row


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rowcount = 0
        self._row = None

    def execute(self, sql, params=()):
        s = " ".join(sql.split()).lower()
        self._row, self.rowcount = None, 0
        if s.startswith("select id, recipient from outreach_messages"):
            provider, pmid = params
            for m in self.db.messages:
                if m["provider"] == provider and m["provider_message_id"] == pmid:
                    self._row = {"id": m["id"], "recipient": m["recipient"]}
                    break
        elif s.startswith("insert into message_events"):
            message_id, peid, etype, payload, occurred_at = params
            if peid not in self.db.events:
                self.db.events[peid] = {"message_id": message_id, "type": etype,
                                        "payload": payload.obj, "occurred_at": occurred_at}
                self._row = {"id": f"e{len(self.db.events)}"}
                self.rowcount = 1
        elif s.startswith("insert into suppression_entries"):
            value, reason, source = params
            if value not in self.db.suppressions:
                self.db.suppressions[value] = {"reason": reason, "source": source}
                self.rowcount = 1
        elif s.startswith("update outreach_messages"):
            (email,) = params
            for m in self.db.messages:
                if m["recipient"].lower() == email and m["status"] == "queued":
                    m["status"] = "cancelled"
                    self.rowcount += 1
        else:  # pragma: no cover
            raise AssertionError(f"unexpected SQL: {s}")

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self, row_factory=None):
        return FakeCursor(self.db)


@pytest.fixture()
def db(monkeypatch):
    fake = FakeDB()

    @contextmanager
    def fake_transaction():
        yield FakeConn(fake)

    monkeypatch.setattr(callbacks, "transaction", fake_transaction)
    return fake


# ------------------------------------------------- 401: signature / time
def test_missing_signature_is_401(client, db):
    assert post(client, event(), send_sig=False).status_code == 401


def test_invalid_signature_is_401(client, db):
    assert post(client, event(), sig="deadbeef").status_code == 401
    assert db.events == {}


def test_signature_made_with_wrong_secret_is_401(client, db):
    body = json.dumps(event()).encode()
    ts = str(int(time.time()))
    r = post(client, None, raw=body, ts=ts, sig=sign(body, ts, key="wrong-secret"))
    assert r.status_code == 401


def test_tampered_body_is_401(client, db):
    ts = str(int(time.time()))
    signed = json.dumps(event()).encode()
    tampered = json.dumps(event(event_type="unsubscribe")).encode()
    r = post(client, None, raw=tampered, ts=ts, sig=sign(signed, ts))
    assert r.status_code == 401
    assert db.suppressions == {}


def test_missing_timestamp_is_401(client, db):
    assert post(client, event(), send_ts=False).status_code == 401


def test_old_timestamp_is_401(client, db):
    assert post(client, event(), ts=str(int(time.time()) - 6 * 60)).status_code == 401


def test_future_timestamp_is_401(client, db):
    assert post(client, event(), ts=str(int(time.time()) + 6 * 60)).status_code == 401


def test_timestamp_inside_window_is_accepted(client, db):
    assert post(client, event(), ts=str(int(time.time()) - 4 * 60)).status_code == 200


def test_iso_timestamp_and_sha256_prefix_are_accepted(client, db):
    body = json.dumps(event()).encode()
    ts = datetime.now(timezone.utc).isoformat()
    r = post(client, None, raw=body, ts=ts, sig="sha256=" + sign(body, ts))
    assert r.status_code == 200


# --------------------------------------------------------- 422: bad body
def test_unknown_event_type_is_422(client, db):
    assert post(client, event(event_type="spam")).status_code == 422


def test_missing_event_id_is_422(client, db):
    bad = event()
    del bad["provider_event_id"]
    assert post(client, bad).status_code == 422


def test_non_json_body_is_422(client, db):
    assert post(client, None, raw=b"not json").status_code == 422


# ------------------------------------------------------- 404: unknown msg
def test_unknown_message_is_404_and_stores_nothing(client, db):
    r = post(client, event(provider_message_id="does_not_exist"))
    assert r.status_code == 404
    assert db.events == {}


# --------------------------------------------- acceptance: duplicate event
def test_duplicate_event_id_is_ignored(client, db):
    first = post(client, event(event_type="opened"))
    second = post(client, event(event_type="opened"))
    assert first.status_code == 200 and first.json()["status"] == "processed"
    assert second.status_code == 200 and second.json()["status"] == "duplicate"
    assert len(db.events) == 1


def test_duplicate_unsubscribe_does_not_suppress_twice(client, db):
    post(client, event(event_type="unsubscribe"))
    again = post(client, event(event_type="unsubscribe"))
    assert again.json()["status"] == "duplicate"
    assert len(db.suppressions) == 1


# ------------------------------------------------ suppression rules
def test_non_suppressing_events_do_not_suppress(client, db):
    for i, t in enumerate(["delivered", "opened", "clicked", "replied"]):
        r = post(client, event(provider_event_id=f"evt_{i}", event_type=t))
        assert r.status_code == 200 and r.json()["suppressed"] is False
    assert db.suppressions == {}


def test_hard_bounce_suppresses_and_cancels_queued(client, db):
    r = post(client, event(event_type="bounce",
                           payload={"bounce_code": "550", "bounce_reason": "mailbox not found"}))
    assert r.status_code == 200
    assert r.json()["suppressed"] is True
    assert r.json()["cancelled_messages"] == 1
    entry = db.suppressions["hr@acme.example.com"]  # stored lower-case
    assert entry["source"] == "bounce"
    assert "mailbox not found" in entry["reason"]
    assert db.messages[1]["status"] == "cancelled"
    assert db.messages[0]["status"] == "sent"  # already-sent message untouched


def test_soft_bounce_does_not_suppress(client, db):
    r = post(client, event(event_type="bounce",
                           payload={"bounce_type": "soft", "bounce_code": "452"}))
    assert r.status_code == 200 and r.json()["suppressed"] is False
    assert db.suppressions == {}


def test_unsubscribe_suppresses(client, db):
    r = post(client, event(event_type="unsubscribe"))
    assert r.json()["suppressed"] is True
    assert db.suppressions["hr@acme.example.com"]["source"] == "unsubscribe"


# ------------------------------------------------ pure helper functions
@pytest.mark.parametrize("payload,expected", [
    ({}, True),
    ({"bounce_code": "550"}, True),
    ({"bounce_code": "5.1.1"}, True),
    ({"bounce_code": "452"}, False),
    ({"bounce_code": "4.2.2"}, False),
    ({"bounce_type": "Hard"}, True),
    ({"bounce_type": "permanent"}, True),
    ({"bounce_type": "soft"}, False),
    ({"bounce_type": "transient"}, False),
    ({"bounce_code": None, "bounce_reason": None}, True),
])
def test_is_hard_bounce(payload, expected):
    assert callbacks.is_hard_bounce(payload) is expected


def test_parse_timestamp_variants():
    assert callbacks.parse_timestamp("1760000000").year == 2025
    assert callbacks.parse_timestamp("1760000000000").year == 2025  # milliseconds
    assert callbacks.parse_timestamp("2026-10-16T12:00:00Z").hour == 12
    assert callbacks.parse_timestamp("2026-10-16T12:00:00").tzinfo is not None
    assert callbacks.parse_timestamp("garbage") is None
    assert callbacks.parse_timestamp("") is None
