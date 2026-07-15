"""Telegram notifier: enabled logic and request/response handling (no real network)."""

import asyncio

import irctc_tui.notify as notify
from irctc_tui.notify import TelegramNotifier


def test_disabled_when_creds_missing():
    assert TelegramNotifier("", "", enabled=True).enabled is False
    assert TelegramNotifier("tok", "", enabled=True).enabled is False
    assert TelegramNotifier("", "123", enabled=True).enabled is False
    assert TelegramNotifier("tok", "123", enabled=True).enabled is True
    assert TelegramNotifier("tok", "123", enabled=False).enabled is False


def test_send_disabled_is_noop():
    ok, detail = asyncio.run(TelegramNotifier("", "").send("hi"))
    assert ok is False and detail == "disabled"


class _FakeResp:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def test_send_sync_builds_request_and_parses_ok(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["data"] = req.data
        return _FakeResp(b'{"ok": true, "result": {"message_id": 1}}')

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    ok, _ = TelegramNotifier("TOKEN", "42", enabled=True)._send_sync("hello world")
    assert ok is True
    assert "botTOKEN/sendMessage" in captured["url"]
    assert b"chat_id=42" in captured["data"]
    assert b"hello" in captured["data"]


def test_send_sync_handles_api_error(monkeypatch):
    def fake_urlopen(req, timeout=0):
        return _FakeResp(b'{"ok": false, "description": "chat not found"}')

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    ok, body = TelegramNotifier("TOKEN", "42", enabled=True)._send_sync("x")
    assert ok is False
    assert "chat not found" in body


def test_send_sync_handles_network_exception(monkeypatch):
    def boom(req, timeout=0):
        raise OSError("no route to host")

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    ok, detail = TelegramNotifier("TOKEN", "42", enabled=True)._send_sync("x")
    assert ok is False
    assert "no route to host" in detail


def test_get_updates_parses_results(monkeypatch):
    def fake_urlopen(url, timeout=0):
        return _FakeResp(b'{"ok": true, "result": [{"update_id": 5, "message": {"text": "status"}}]}')

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    updates = TelegramNotifier("TOK", "42", enabled=True)._get_updates_sync(None, 0)
    assert len(updates) == 1
    assert updates[0]["update_id"] == 5
    assert updates[0]["message"]["text"] == "status"


def test_get_updates_disabled_returns_empty():
    assert asyncio.run(TelegramNotifier("", "").get_updates()) == []


def test_get_updates_swallows_errors(monkeypatch):
    def boom(url, timeout=0):
        raise OSError("down")

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    assert TelegramNotifier("TOK", "42", enabled=True)._get_updates_sync(3, 0) == []
