"""Tests for the USB monitoring client.

Everything runs on macOS without pyudev, root, a running server, a LAN, or a
physical USB device. HTTP is faked and databases are temporary.
"""

import io
import json
import threading
import time
from urllib.error import HTTPError, URLError

import pytest

from client.agent import Agent
from client.config import ConfigError, load_config
from client.monitor import (
    DeviceEvent,
    LinuxUsbMonitor,
    SyntheticMonitor,
    classify_device,
    device_event_from_properties,
    make_synthetic_event,
)
from client.outbox import Outbox
from client.sender import DeliveryOutcome, EventSender, HeartbeatSender

URLOPEN = "client.sender.urllib_request.urlopen"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _base_env(**overrides):
    env = {
        "COMPUTER_ID": "LAB-PC-01",
        "SERVER_URL": "http://192.168.1.10:5000",
        "AUTH_TOKEN": "test-token",
    }
    env.update(overrides)
    return env


def _client_config(tmp_path, **overrides):
    overrides.setdefault("CLIENT_DB_PATH", str(tmp_path / "outbox.db"))
    overrides.setdefault("RETRY_INTERVAL", "1")
    overrides.setdefault("REQUEST_TIMEOUT", "1")
    return load_config(_base_env(**overrides))


class FakeResponse:
    def __init__(self, status, body=b""):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def close(self):
        pass


def _http_error(code):
    return HTTPError(
        "http://server:5000/api/events", code, "error", {}, io.BytesIO(b"")
    )


class FakeSender:
    """Records events and returns a fixed outcome."""

    def __init__(self, outcome, detail="ok"):
        self.outcome = outcome
        self.detail = detail
        self.sent = []

    def send(self, event):
        self.sent.append(event)
        return self.outcome, self.detail


class FakeHeartbeatSender:
    """Records heartbeats and returns a fixed outcome."""

    def __init__(self, outcome=DeliveryOutcome.DELIVERED, detail="ok"):
        self.outcome = outcome
        self.detail = detail
        self.sent = []

    def send(self, computer_id):
        self.sent.append(computer_id)
        return self.outcome, self.detail


class SequenceHeartbeatSender:
    """Returns queued outcomes in order, then repeats the last one."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.sent = []

    def send(self, computer_id):
        self.sent.append(computer_id)
        if len(self.outcomes) > 1:
            return self.outcomes.pop(0)
        return self.outcomes[0]


@pytest.fixture()
def outbox(tmp_path):
    box = Outbox(str(tmp_path / "outbox.db"))
    box.initialize()
    return box


# --------------------------------------------------------------------------- #
# 1-2. Configuration validation
# --------------------------------------------------------------------------- #

def test_config_loads_required_values_and_defaults():
    config = load_config(_base_env())
    assert config.computer_id == "LAB-PC-01"
    assert config.server_url == "http://192.168.1.10:5000"
    assert config.events_url == "http://192.168.1.10:5000/api/events"
    assert config.heartbeat_url == "http://192.168.1.10:5000/api/heartbeat"
    assert config.retry_interval == 5.0
    assert config.request_timeout == 10.0
    assert config.heartbeat_interval == 30.0


def test_custom_heartbeat_interval_is_loaded():
    config = load_config(_base_env(HEARTBEAT_INTERVAL_SECONDS="12.5"))
    assert config.heartbeat_interval == 12.5


def test_invalid_heartbeat_interval_is_rejected():
    with pytest.raises(ConfigError):
        load_config(_base_env(HEARTBEAT_INTERVAL_SECONDS="0"))


@pytest.mark.parametrize("missing", ["COMPUTER_ID", "SERVER_URL", "AUTH_TOKEN"])
def test_missing_required_setting_is_rejected(missing):
    env = _base_env()
    del env[missing]
    with pytest.raises(ConfigError):
        load_config(env)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://host:5000/", "http://host:5000"),
        ("http://host:5000/api/events", "http://host:5000"),
        ("https://host/api/events/", "https://host"),
        ("http://host:5000/api/heartbeat", "http://host:5000"),
    ],
)
def test_server_url_is_normalized(raw, expected):
    config = load_config(_base_env(SERVER_URL=raw))
    assert config.server_url == expected
    assert config.events_url == expected + "/api/events"
    assert config.heartbeat_url == expected + "/api/heartbeat"


def test_server_url_without_scheme_is_rejected():
    with pytest.raises(ConfigError):
        load_config(_base_env(SERVER_URL="192.168.1.10:5000"))


def test_invalid_retry_interval_is_rejected():
    with pytest.raises(ConfigError):
        load_config(_base_env(RETRY_INTERVAL="not-a-number"))


# --------------------------------------------------------------------------- #
# 3-4. Outbox persistence and stable event ids
# --------------------------------------------------------------------------- #

def test_outbox_initialization_and_persistence(outbox):
    assert outbox.pending_count() == 0
    event_id = outbox.generate_event_id()
    assert outbox.enqueue(
        event_id, "LAB-PC-01", "connected", "2026-10-11T14:07:00Z",
        device_category="usb_storage",
    )
    pending = outbox.pending()
    assert len(pending) == 1
    assert pending[0]["event_id"] == event_id
    assert pending[0]["computer_id"] == "LAB-PC-01"


def test_outbox_persists_across_instances(tmp_path):
    path = str(tmp_path / "outbox.db")
    first = Outbox(path)
    first.initialize()
    first.enqueue("evt-1", "LAB-PC-01", "connected", "2026-10-11T14:07:00Z")

    second = Outbox(path)
    second.initialize()
    assert second.pending_count() == 1


def test_event_id_is_preserved_across_retries(outbox):
    event_id = outbox.generate_event_id()
    outbox.enqueue(event_id, "LAB-PC-01", "connected", "2026-10-11T14:07:00Z")

    outbox.record_failure(event_id, "server unreachable")
    outbox.record_failure(event_id, "server unreachable")

    pending = outbox.pending()
    assert pending[0]["event_id"] == event_id
    assert pending[0]["attempts"] == 2
    assert pending[0]["last_error"] == "server unreachable"


def test_mark_delivered_removes_event(outbox):
    event_id = outbox.generate_event_id()
    outbox.enqueue(event_id, "LAB-PC-01", "connected", "2026-10-11T14:07:00Z")
    assert outbox.mark_delivered(event_id) is True
    assert outbox.pending_count() == 0


# --------------------------------------------------------------------------- #
# 10. Invalid event types rejected before sending
# --------------------------------------------------------------------------- #

def test_outbox_rejects_invalid_event_type(outbox):
    with pytest.raises(ValueError):
        outbox.enqueue("evt-x", "LAB-PC-01", "exploded", "2026-10-11T14:07:00Z")


def test_sender_rejects_invalid_event_type():
    sender = EventSender("http://server:5000/api/events", "tok", 5.0)
    outcome, _ = sender.send({"event_type": "exploded"})
    assert outcome == DeliveryOutcome.PERMANENT


# --------------------------------------------------------------------------- #
# 5-9. HTTP delivery outcomes
# --------------------------------------------------------------------------- #

@pytest.fixture()
def queued_event(outbox):
    event_id = outbox.generate_event_id()
    outbox.enqueue(
        event_id, "LAB-PC-01", "connected", "2026-10-11T14:07:00Z",
        device_category="usb_storage", vendor_id="0781", product_id="5567",
        device_name="SanDisk Cruzer",
    )
    return outbox, outbox.pending()[0]


def _sender():
    return EventSender("http://server:5000/api/events", "tok", 5.0)


def test_http_201_is_delivered(monkeypatch, queued_event):
    _, event = queued_event
    monkeypatch.setattr(URLOPEN, lambda req, timeout: FakeResponse(201))
    outcome, detail = _sender().send(event)
    assert outcome == DeliveryOutcome.DELIVERED
    assert detail == "created"


def test_http_200_duplicate_is_delivered(monkeypatch, queued_event):
    _, event = queued_event
    monkeypatch.setattr(URLOPEN, lambda req, timeout: FakeResponse(200))
    outcome, detail = _sender().send(event)
    assert outcome == DeliveryOutcome.DELIVERED
    assert detail == "duplicate"


def test_connection_failure_is_transient(monkeypatch, queued_event):
    _, event = queued_event

    def refuse(req, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(URLOPEN, refuse)
    outcome, _ = _sender().send(event)
    assert outcome == DeliveryOutcome.TRANSIENT


def test_http_500_is_transient(monkeypatch, queued_event):
    _, event = queued_event

    def server_error(req, timeout):
        raise _http_error(500)

    monkeypatch.setattr(URLOPEN, server_error)
    outcome, _ = _sender().send(event)
    assert outcome == DeliveryOutcome.TRANSIENT


def test_http_401_is_auth_error(monkeypatch, queued_event):
    _, event = queued_event

    def unauthorized(req, timeout):
        raise _http_error(401)

    monkeypatch.setattr(URLOPEN, unauthorized)
    outcome, detail = _sender().send(event)
    assert outcome == DeliveryOutcome.AUTH_ERROR
    assert "auth" in detail.lower()


def test_http_400_is_permanent(monkeypatch, queued_event):
    _, event = queued_event

    def bad_request(req, timeout):
        raise _http_error(400)

    monkeypatch.setattr(URLOPEN, bad_request)
    outcome, _ = _sender().send(event)
    assert outcome == DeliveryOutcome.PERMANENT


def test_request_uses_bearer_token_and_clean_payload(monkeypatch, queued_event):
    _, event = queued_event
    captured = {}

    def capture(req, timeout):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = req.data
        return FakeResponse(201)

    monkeypatch.setattr(URLOPEN, capture)
    _sender().send(event)

    assert captured["url"] == "http://server:5000/api/events"
    assert captured["auth"] == "Bearer tok"
    payload = json.loads(captured["body"])
    assert set(payload) == {
        "event_id", "computer_id", "event_type", "device_category",
        "vendor_id", "product_id", "device_name", "occurred_at",
    }
    assert "attempts" not in payload


def test_delivered_event_is_removed_by_agent(tmp_path):
    config = _client_config(tmp_path)
    box = Outbox(config.client_db_path)
    box.initialize()
    agent = Agent(config, SyntheticMonitor([]), box, FakeSender(DeliveryOutcome.DELIVERED))
    agent.enqueue_device_event(DeviceEvent("connected", device_name="X"))

    assert agent.deliver_pending() == 1
    assert box.pending_count() == 0


def test_connection_failure_retains_event_and_increments_attempts(monkeypatch, tmp_path):
    config = _client_config(tmp_path)
    box = Outbox(config.client_db_path)
    box.initialize()
    sender = EventSender(config.events_url, config.auth_token, config.request_timeout)
    agent = Agent(config, SyntheticMonitor([]), box, sender)
    agent.enqueue_device_event(DeviceEvent("connected", device_name="X"))

    def refuse(req, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(URLOPEN, refuse)
    assert agent.deliver_pending() == 0
    assert box.pending_count() == 1
    assert box.pending()[0]["attempts"] == 1


def test_http_5xx_retains_event(monkeypatch, tmp_path):
    config = _client_config(tmp_path)
    box = Outbox(config.client_db_path)
    box.initialize()
    sender = EventSender(config.events_url, config.auth_token, config.request_timeout)
    agent = Agent(config, SyntheticMonitor([]), box, sender)
    agent.enqueue_device_event(DeviceEvent("connected", device_name="X"))

    def server_error(req, timeout):
        raise _http_error(503)

    monkeypatch.setattr(URLOPEN, server_error)
    assert agent.deliver_pending() == 0
    assert box.pending_count() == 1


def test_http_401_retains_event_and_flags_auth(monkeypatch, tmp_path, caplog):
    config = _client_config(tmp_path)
    box = Outbox(config.client_db_path)
    box.initialize()
    sender = EventSender(config.events_url, config.auth_token, config.request_timeout)
    agent = Agent(config, SyntheticMonitor([]), box, sender)
    agent.enqueue_device_event(DeviceEvent("connected", device_name="X"))

    def unauthorized(req, timeout):
        raise _http_error(401)

    monkeypatch.setattr(URLOPEN, unauthorized)
    with caplog.at_level("WARNING", logger="client.agent"):
        assert agent.deliver_pending() == 0

    assert box.pending_count() == 1
    assert any("Authentication problem" in record.message for record in caplog.records)


# --------------------------------------------------------------------------- #
# 11. Fake monitor events become correctly formatted queued events
# --------------------------------------------------------------------------- #

def test_fake_monitor_events_become_queued_events(tmp_path):
    config = _client_config(tmp_path)
    box = Outbox(config.client_db_path)
    box.initialize()
    device = DeviceEvent(
        "disconnected", device_category="usb_storage",
        vendor_id="0781", product_id="5567", device_name="Cruzer",
    )
    agent = Agent(config, SyntheticMonitor([device]), box, FakeSender(DeliveryOutcome.DELIVERED))

    agent._consume_monitor()

    pending = box.pending()
    assert len(pending) == 1
    row = pending[0]
    assert row["event_type"] == "disconnected"
    assert row["device_category"] == "usb_storage"
    assert row["computer_id"] == "LAB-PC-01"
    assert row["vendor_id"] == "0781"
    assert row["product_id"] == "5567"
    assert row["occurred_at"].endswith("Z")
    assert row["event_id"]


def test_synthetic_event_is_clearly_labelled():
    event = make_synthetic_event("connected")
    assert event.device_name.startswith("[SYNTHETIC-TEST]")


# --------------------------------------------------------------------------- #
# 12. Monitor module import and platform handling
# --------------------------------------------------------------------------- #

def test_monitor_module_imports_without_pyudev():
    # Reaching this point already proves the import succeeded on macOS.
    assert callable(classify_device)
    assert callable(device_event_from_properties)


def test_linux_monitor_raises_clear_error_off_linux(monkeypatch):
    import client.monitor as monitor_module

    monkeypatch.setattr(monitor_module.sys, "platform", "darwin")
    with pytest.raises(RuntimeError) as excinfo:
        LinuxUsbMonitor()
    assert "Linux" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 13. Device classification
# --------------------------------------------------------------------------- #

def test_classify_storage_by_driver():
    assert classify_device({"ID_USB_DRIVER": "usb-storage"}) == "usb_storage"


def test_classify_storage_by_interface_class():
    assert classify_device({"ID_USB_INTERFACES": ":080650:"}) == "usb_storage"


def test_classify_peripheral_by_interface_class():
    assert classify_device({"ID_USB_INTERFACES": ":030101:"}) == "usb_peripheral"


def test_classify_peripheral_by_driver():
    assert classify_device({"ID_USB_DRIVER": "usbhid"}) == "usb_peripheral"


def test_classify_unknown_when_metadata_missing():
    assert classify_device({}) == "unknown"
    assert classify_device({"ID_VENDOR_ID": "1234"}) == "unknown"


def test_device_event_from_properties_builds_payload():
    event = device_event_from_properties("add", {
        "ID_VENDOR_ID": "0781",
        "ID_MODEL_ID": "5567",
        "ID_MODEL": "Cruzer",
        "ID_USB_INTERFACES": ":080650:",
    })
    assert event.event_type == "connected"
    assert event.device_category == "usb_storage"
    assert event.vendor_id == "0781"
    assert event.product_id == "5567"
    assert event.device_name == "Cruzer"


def test_device_event_remove_and_unknown_action():
    assert device_event_from_properties("remove", {}).event_type == "disconnected"
    assert device_event_from_properties("change", {}) is None


# --------------------------------------------------------------------------- #
# 14. Shutdown does not corrupt the queue
# --------------------------------------------------------------------------- #

def test_shutdown_does_not_corrupt_queue(tmp_path):
    config = _client_config(tmp_path)
    box = Outbox(config.client_db_path)
    box.initialize()
    agent = Agent(config, SyntheticMonitor([]), box, FakeSender(DeliveryOutcome.TRANSIENT, "down"))

    for index in range(3):
        agent.enqueue_device_event(DeviceEvent("connected", device_name=f"dev{index}"))

    agent.stop()

    reopened = Outbox(config.client_db_path)
    reopened.initialize()
    assert reopened.pending_count() == 3


# --------------------------------------------------------------------------- #
# 15. Heartbeat delivery and independence from USB events
# --------------------------------------------------------------------------- #

def _heartbeat_sender():
    return HeartbeatSender("http://server:5000/api/heartbeat", "tok", 5.0)


def test_heartbeat_sender_success_uses_bearer_token(monkeypatch):
    captured = {}

    def capture(req, timeout):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = req.data
        return FakeResponse(200)

    monkeypatch.setattr(URLOPEN, capture)
    outcome, _ = _heartbeat_sender().send("LAB-PC-01")

    assert outcome == DeliveryOutcome.DELIVERED
    assert captured["url"] == "http://server:5000/api/heartbeat"
    assert captured["auth"] == "Bearer tok"
    assert json.loads(captured["body"]) == {"computer_id": "LAB-PC-01"}


def test_heartbeat_connection_failure_is_transient(monkeypatch):
    def refuse(req, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(URLOPEN, refuse)
    outcome, _ = _heartbeat_sender().send("LAB-PC-01")
    assert outcome == DeliveryOutcome.TRANSIENT


def test_heartbeat_auth_error_is_reported(monkeypatch):
    def unauthorized(req, timeout):
        raise _http_error(401)

    monkeypatch.setattr(URLOPEN, unauthorized)
    outcome, _ = _heartbeat_sender().send("LAB-PC-01")
    assert outcome == DeliveryOutcome.AUTH_ERROR


def test_agent_sends_heartbeat_without_usb_activity(tmp_path):
    config = _client_config(tmp_path)
    box = Outbox(config.client_db_path)
    box.initialize()
    heartbeat = FakeHeartbeatSender()
    agent = Agent(config, SyntheticMonitor([]), box,
                  FakeSender(DeliveryOutcome.DELIVERED), heartbeat_sender=heartbeat)

    assert agent.send_heartbeat() == DeliveryOutcome.DELIVERED
    assert heartbeat.sent == ["LAB-PC-01"]
    assert box.pending_count() == 0  # heartbeats never touch the event outbox


def test_agent_without_heartbeat_sender_is_noop(tmp_path):
    config = _client_config(tmp_path)
    box = Outbox(config.client_db_path)
    box.initialize()
    agent = Agent(config, SyntheticMonitor([]), box, FakeSender(DeliveryOutcome.DELIVERED))
    assert agent.send_heartbeat() is None


def test_heartbeat_transient_failure_is_retried_next_cycle(tmp_path):
    config = _client_config(tmp_path)
    box = Outbox(config.client_db_path)
    box.initialize()
    heartbeat = SequenceHeartbeatSender(
        [(DeliveryOutcome.TRANSIENT, "down"), (DeliveryOutcome.DELIVERED, "ok")]
    )
    agent = Agent(config, SyntheticMonitor([]), box,
                  FakeSender(DeliveryOutcome.DELIVERED), heartbeat_sender=heartbeat)

    assert agent.send_heartbeat() == DeliveryOutcome.TRANSIENT
    assert agent.send_heartbeat() == DeliveryOutcome.DELIVERED
    assert len(heartbeat.sent) == 2


def test_heartbeat_failure_does_not_block_event_delivery(tmp_path):
    config = _client_config(tmp_path)
    box = Outbox(config.client_db_path)
    box.initialize()
    heartbeat = FakeHeartbeatSender(DeliveryOutcome.TRANSIENT, "down")
    agent = Agent(config, SyntheticMonitor([]), box,
                  FakeSender(DeliveryOutcome.DELIVERED), heartbeat_sender=heartbeat)
    agent.enqueue_device_event(DeviceEvent("connected", device_name="X"))

    assert agent.send_heartbeat() == DeliveryOutcome.TRANSIENT
    assert agent.deliver_pending() == 1
    assert box.pending_count() == 0


def test_event_failure_does_not_block_heartbeat(tmp_path):
    config = _client_config(tmp_path)
    box = Outbox(config.client_db_path)
    box.initialize()
    agent = Agent(config, SyntheticMonitor([]), box,
                  FakeSender(DeliveryOutcome.TRANSIENT, "down"),
                  heartbeat_sender=FakeHeartbeatSender())
    agent.enqueue_device_event(DeviceEvent("connected", device_name="X"))

    assert agent.deliver_pending() == 0
    assert box.pending_count() == 1
    assert agent.send_heartbeat() == DeliveryOutcome.DELIVERED


def test_heartbeat_thread_runs_and_stops_cleanly(tmp_path):
    config = _client_config(tmp_path, RETRY_INTERVAL="0.05",
                            HEARTBEAT_INTERVAL_SECONDS="0.05")
    box = Outbox(config.client_db_path)
    box.initialize()
    heartbeat = FakeHeartbeatSender()
    agent = Agent(config, SyntheticMonitor([]), box,
                  FakeSender(DeliveryOutcome.DELIVERED), heartbeat_sender=heartbeat)

    runner = threading.Thread(target=agent.run, daemon=True)
    runner.start()
    time.sleep(0.3)
    agent.stop()
    runner.join(timeout=5)

    assert not runner.is_alive()
    assert heartbeat.sent  # ran independently of any USB event
    assert agent._heartbeat_thread is not None
    assert not agent._heartbeat_thread.is_alive()
    assert agent._monitor_thread is not None
    assert not agent._monitor_thread.is_alive()
