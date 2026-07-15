"""Telegram notifications to the owner's chat.

Sends short alerts (seat found, payment hand-off, errors) to a Telegram chat via
the Bot API, so you can step away from the machine and still get pinged.

Uses only the standard library (``urllib``) — no extra dependency. The blocking
HTTP call is wrapped with :func:`asyncio.to_thread` so it never stalls the TUI.

Setup
-----
1. Message **@BotFather**, ``/newbot``, and copy the **bot token**.
2. Message **@userinfobot** (or your new bot, then read
   ``api.telegram.org/bot<token>/getUpdates``) to find your numeric **owner id**.
3. Put both in the Telegram tab (or ``config.json``) and press *Test Telegram*.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request

_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """Sends messages to one owner chat. Safe to construct with blank creds."""

    def __init__(self, bot_token: str, owner_id: str, enabled: bool = True) -> None:
        self.bot_token = (bot_token or "").strip()
        self.owner_id = (owner_id or "").strip()
        self.enabled = bool(enabled and self.bot_token and self.owner_id)

    # -- sync core ------------------------------------------------------- #

    def _send_sync(self, text: str, timeout: float = 15.0) -> tuple[bool, str]:
        url = _API.format(token=self.bot_token)
        data = urllib.parse.urlencode(
            {"chat_id": self.owner_id, "text": text, "disable_web_page_preview": "true"}
        ).encode()
        req = urllib.request.Request(url, data=data)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace") if exc.fp else str(exc)
            return False, f"HTTP {exc.code}: {detail}"
        except Exception as exc:  # noqa: BLE001 - network/DNS/timeout
            return False, str(exc)
        try:
            ok = bool(json.loads(body).get("ok"))
        except ValueError:
            return False, body
        return ok, body

    # -- async wrapper --------------------------------------------------- #

    async def send(self, text: str) -> tuple[bool, str]:
        """Return ``(ok, detail)``. No-op (``False, "disabled"``) if not configured."""
        if not self.enabled:
            return False, "disabled"
        return await asyncio.to_thread(self._send_sync, text)

    # -- polling (two-way remote control) -------------------------------- #

    def _get_updates_sync(self, offset: int | None, timeout: int) -> list[dict]:
        params: dict = {"timeout": timeout, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=timeout + 15) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 - polling must never crash the caller
            return []
        return data.get("result", []) if data.get("ok") else []

    async def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict]:
        """Fetch new updates. ``offset`` acknowledges everything before it."""
        if not self.enabled:
            return []
        return await asyncio.to_thread(self._get_updates_sync, offset, timeout)

    # -- photo (send a browser screenshot to the owner) ------------------ #

    def _send_photo_sync(self, path: str, caption: str = "") -> tuple[bool, str]:
        try:
            with open(path, "rb") as fh:
                img = fh.read()
        except OSError as exc:
            return False, f"cannot read {path}: {exc}"
        boundary = "----IRCTCTUIboundaryZ7xQ2aB"
        parts: list[bytes] = []

        def field(name: str, value: str) -> None:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            )

        field("chat_id", self.owner_id)
        if caption:
            field("caption", caption)
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
            f'filename="{os.path.basename(path)}"\r\nContent-Type: image/png\r\n\r\n'.encode()
        )
        parts.append(img)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendPhoto",
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        return bool(data.get("ok")), "" if data.get("ok") else str(data)

    async def send_photo(self, path: str, caption: str = "") -> tuple[bool, str]:
        if not self.enabled:
            return False, "disabled"
        return await asyncio.to_thread(self._send_photo_sync, path, caption)


def from_config(telegram_cfg) -> TelegramNotifier:
    """Build a notifier from a :class:`~irctc_tui.config.TelegramConfig`."""
    return TelegramNotifier(
        telegram_cfg.bot_token, telegram_cfg.owner_id, enabled=telegram_cfg.enabled
    )
