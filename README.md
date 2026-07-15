# 🚆 IRCTC Tatkal TUI

A **terminal user interface** that automates the tedious parts of booking an
[IRCTC](https://www.irctc.co.in/eticket/train-search) train ticket — station
entry, journey date, quota/class selection, passenger details, and **Tatkal
availability polling at a custom interval** — while leaving the parts that must
stay human (the **CAPTCHA**, the **login**, and the **payment**) to you.

Built with [Playwright](https://playwright.dev/python/) (headed Chromium) and
[Textual](https://textual.textualize.io/).

> ⚠️ **Read [Responsible use](#-responsible-use) before running.** This tool
> fills forms and polls availability. It does **not** solve CAPTCHAs, does
> **not** submit payment, and does **not** bypass any bot protection — those
> steps are handed back to you in the browser.

---

## Why

Tatkal tickets sell out in seconds. The bottleneck isn't the booking itself —
it's typing the same journey and passenger details fast enough, and refreshing
availability at the right moment. This tool keeps all of that pre-entered and
ready, polls availability on an interval you choose, and drives the browser up
to the CAPTCHA so you only do the human bits.

## What it does

- **Everything is enterable** from the TUI: from/to stations, journey date,
  class, quota (Tatkal / Premium Tatkal / General / …), target train, an
  unlimited passenger list, your IRCTC username, the polling interval, a
  scheduled start time, retry limits, and more.
- **Headed browser** so you can watch every step and take over instantly.
- **Custom availability polling** — check every *X* seconds until a seat opens.
- **Scheduled start** — arm it before 10:00/11:00 AM and let it fire the moment
  the Tatkal window opens.
- **Human-in-the-loop by design** — it pauses and waits for *you* at login,
  CAPTCHA, and payment.
- **Config saved to disk** (git-ignored) so you never re-type your journey.

## Quick start

```bash
git clone https://github.com/nickthelegend/irctc-tatkal-tui.git
cd irctc-tatkal-tui

# install (uv recommended; pip works too)
uv venv && source .venv/bin/activate
uv pip install -e .
playwright install chromium

# launch the TUI
irctc-tui
```

Full docs, configuration reference, and the booking flow are below — see
[Configuration](#configuration) and [How the booking flow works](#how-the-booking-flow-works).

## 🛡 Responsible use

- This project is for **your own** bookings. Automated booking may be against
  [IRCTC's terms of service](https://www.irctc.co.in/) — you are responsible for
  how you use it.
- It **never** solves CAPTCHAs or bypasses bot detection. Those exist on
  purpose; the tool stops and lets you do them by hand.
- It **never** enters payment credentials or submits a payment. You complete
  payment yourself in the browser.
- Keep your polling interval reasonable. Hammering IRCTC with sub-second
  requests is abusive and will get you blocked.
- Your `config.json` can contain your IRCTC username (and optionally password).
  It is **git-ignored**. Treat it like a password file.

## License

[MIT](LICENSE) © 2026 Nivesh Gajengi
