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


def from_config(telegram_cfg) -> TelegramNotifier:
    """Build a notifier from a :class:`~irctc_tui.config.TelegramConfig`."""
    return TelegramNotifier(
        telegram_cfg.bot_token, telegram_cfg.owner_id, enabled=telegram_cfg.enabled
    )
