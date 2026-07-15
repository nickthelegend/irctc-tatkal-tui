"""Alarm sound generation and player construction (no audio is actually played)."""

import wave

from irctc_tui.alarm import AlarmPlayer, ensure_default_sound, generate_tune_wav


def test_generate_tune_wav_is_valid(tmp_path):
    path = tmp_path / "alarm.wav"
    generate_tune_wav(path)
    assert path.exists() and path.stat().st_size > 1000
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 44_100
        # melody is a few seconds long
        assert 1.0 < w.getnframes() / w.getframerate() < 6.0


def test_ensure_default_sound_caches(tmp_path, monkeypatch):
    import irctc_tui.alarm as alarm_mod

    monkeypatch.setattr(alarm_mod, "_cache_dir", lambda: tmp_path)
    p1 = ensure_default_sound()
    mtime1 = p1.stat().st_mtime
    p2 = ensure_default_sound()  # second call must reuse, not regenerate
    assert p1 == p2
    assert p2.stat().st_mtime == mtime1


def test_alarm_player_constructs_without_playing(tmp_path):
    sound = tmp_path / "s.wav"
    generate_tune_wav(sound)
    player = AlarmPlayer(sound)
    assert isinstance(player.available, bool)
    assert player.playing is False
    # stop() on a never-started player is a harmless no-op
    player.stop()
    assert player.playing is False
