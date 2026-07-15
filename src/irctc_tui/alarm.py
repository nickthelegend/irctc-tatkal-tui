"""A looping completion alarm.

When the booker finds a seat / reaches the payment hand-off, it rings a tune on
**loop until you silence it** — so you can step away and let it call you back like
an alarm clock.

* No copyrighted audio is bundled. A short, pleasant chime is **synthesised on
  first use** into ``~/.cache/irctc-tui/alarm.wav`` (pure stdlib, no deps).
* Point ``alarm_sound_path`` at any ``.wav``/``.mp3`` to use your own song.
* Playback shells out to the OS player (``afplay`` on macOS, ``paplay``/``aplay``/
  ``ffplay``/``mpg123`` on Linux, ``winsound`` on Windows) and loops in a
  background thread, so it never blocks the TUI.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import sys
import threading
import wave
from pathlib import Path

SAMPLE_RATE = 44_100

# Equal-temperament note frequencies (A4 = 440 Hz).
_NOTES = {
    "C5": 523.25, "D5": 587.33, "E5": 659.25, "F5": 698.46, "G5": 783.99,
    "A5": 880.00, "B5": 987.77, "C6": 1046.50, "E6": 1318.51, "G6": 1567.98,
}

# (note or None for rest, seconds). A cheerful rising "ta-da-daa!" that resolves,
# then a beat of silence so the loop doesn't feel frantic.
_MELODY = [
    ("C5", 0.16), ("E5", 0.16), ("G5", 0.16), ("C6", 0.32),
    (None, 0.08), ("G5", 0.16), ("C6", 0.40),
    (None, 0.55),
]


def _cache_dir() -> Path:
    base = Path.home() / ".cache" / "irctc-tui"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _synth_note(freq: float, dur: float, amp: float = 0.5) -> list[float]:
    """One note with a click-free attack/release envelope and a soft harmonic."""
    n = int(SAMPLE_RATE * dur)
    attack, release = 0.008, 0.06
    samples: list[float] = []
    for i in range(n):
        t = i / SAMPLE_RATE
        if t < attack:
            env = t / attack
        elif t > dur - release:
            env = max(0.0, (dur - t) / release)
        else:
            env = 1.0
        tone = math.sin(2 * math.pi * freq * t) + 0.3 * math.sin(4 * math.pi * freq * t)
        samples.append(env * amp * tone / 1.3)
    return samples


def generate_tune_wav(path: Path) -> Path:
    """Synthesise the built-in completion tune to ``path`` (16-bit mono WAV)."""
    frames: list[float] = []
    for note, dur in _MELODY:
        if note is None:
            frames.extend([0.0] * int(SAMPLE_RATE * dur))
        else:
            frames.extend(_synth_note(_NOTES[note], dur))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        pcm = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in frames)
        w.writeframes(pcm)
    return path


def ensure_default_sound() -> Path:
    """Return the built-in tune, generating it once if needed."""
    path = _cache_dir() / "alarm.wav"
    if not path.exists() or path.stat().st_size == 0:
        generate_tune_wav(path)
    return path


def _play_once_command(path: str) -> list[str] | None:
    """The OS command to play ``path`` a single time, or None if none is found."""
    if sys.platform == "darwin":
        return ["afplay", path]
    if sys.platform.startswith("win"):
        return None  # handled by winsound in the player thread
    for player, args in (
        ("paplay", [path]),
        ("aplay", ["-q", path]),
        ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet", path]),
        ("mpg123", ["-q", path]),
    ):
        if shutil.which(player):
            return [player, *args]
    return None


class AlarmPlayer:
    """Loops a sound in a daemon thread until :meth:`stop` is called."""

    def __init__(self, sound_path: str | Path | None = None) -> None:
        self.sound_path = str(sound_path) if sound_path else str(ensure_default_sound())
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self.available = self._detect_available()

    def _detect_available(self) -> bool:
        if sys.platform.startswith("win"):
            return True  # winsound ships with CPython on Windows
        return _play_once_command(self.sound_path) is not None

    @property
    def playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def start(self) -> None:
        """Begin (or restart) the loop. No-op if already ringing."""
        if self.playing:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="alarm", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Silence the alarm and stop looping."""
        self._stop.set()
        if sys.platform.startswith("win"):
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:  # noqa: BLE001
                pass
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    # -- internals ------------------------------------------------------- #

    def _loop(self) -> None:
        if sys.platform.startswith("win"):
            self._loop_windows()
            return
        cmd = _play_once_command(self.sound_path)
        if cmd is None:
            self._loop_bell()
            return
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except FileNotFoundError:
                self._loop_bell()
                return
            while self._proc.poll() is None:
                if self._stop.wait(0.15):
                    try:
                        self._proc.terminate()
                    except Exception:  # noqa: BLE001
                        pass
                    return

    def _loop_windows(self) -> None:
        try:
            import winsound
        except Exception:  # noqa: BLE001
            self._loop_bell()
            return
        # SND_LOOP loops natively until we purge it.
        try:
            winsound.PlaySound(
                self.sound_path,
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
            )
        except Exception:  # noqa: BLE001
            self._loop_bell()
            return
        self._stop.wait()  # block the thread until stop(); PlaySound loops meanwhile

    def _loop_bell(self) -> None:
        """Last resort: ring the terminal bell once a second."""
        while not self._stop.is_set():
            try:
                sys.stdout.write("\a")
                sys.stdout.flush()
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(1.0)


if __name__ == "__main__":  # quick manual test: python -m irctc_tui.alarm
    print("Generating tune →", ensure_default_sound())
