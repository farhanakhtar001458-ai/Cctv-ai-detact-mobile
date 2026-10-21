"""Text-to-speech for phone-use announcements.

Two output paths are supported and both require explicit configuration:

``dashboard`` (default) - the backend sends the announcement text over the
WebSocket and the authorised monitoring browser speaks it with the Web Speech
API. The operator must enable audio once per session (a browser gesture), which
also satisfies the "explicitly authorised" requirement.

``server`` - the backend synthesises audio locally. Engines are probed in order:
``pyttsx3``, ``espeak-ng``/``espeak``, macOS ``say``. Playback happens on the
host audio device (``aplay``/``afplay``/``ffplay``) when available, and the
generated file is exposed at ``/api/voice/clip/{name}`` so it can be routed to
an authorised classroom speaker system if that is configured separately.

The service never talks to a camera speaker: that requires an explicit
integration (see docs/DEPLOYMENT.md) and is out of scope by design.
"""

from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import redact
from app.services.broadcast import broadcaster

log = logging.getLogger(__name__)

MAX_HISTORY = 25


def _which(*names: str) -> str | None:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


@dataclass
class VoiceEngineInfo:
    available: bool
    engine: str
    can_synthesize: bool
    can_play: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "engine": self.engine,
            "can_synthesize": self.can_synthesize,
            "can_play": self.can_play,
            "detail": self.detail,
        }


def detect_engine() -> VoiceEngineInfo:
    espeak = _which("espeak-ng", "espeak")
    say = _which("say") if os.name == "posix" else None
    if espeak:
        player = _which("aplay", "ffplay", "play")
        return VoiceEngineInfo(
            available=True,
            engine="espeak",
            can_synthesize=True,
            can_play=bool(player),
            detail=f"espeak at {espeak}; player={player or 'none'}",
        )
    if say:
        player = _which("afplay")
        return VoiceEngineInfo(
            available=True,
            engine="say",
            can_synthesize=True,
            can_play=bool(player),
            detail=f"say at {say}; player={player or 'none'}",
        )
    try:
        import pyttsx3  # noqa: F401

        pyttsx3.init()
        return VoiceEngineInfo(True, "pyttsx3", True, True, "pyttsx3 available")
    except Exception:  # noqa: BLE001
        pass
    return VoiceEngineInfo(
        available=False,
        engine="none",
        can_synthesize=False,
        can_play=False,
        detail="No server audio engine found. Use dashboard (browser) announcements.",
    )


@dataclass
class Announcement:
    text: str
    camera_id: int | None
    camera_name: str
    event_id: int | None
    category: str
    output: str
    created_at: float = field(default_factory=time.time)
    spoken: bool = False
    audio_url: str | None = None
    error: str = ""


class VoiceService:
    def __init__(self) -> None:
        self.engine = detect_engine()
        self._queue: queue.Queue[Announcement | None] = queue.Queue(maxsize=50)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._history: list[Announcement] = []
        self._recent_times: list[float] = []
        self._clips_dir = settings.data_dir / "voice"
        self._clips_dir.mkdir(parents=True, exist_ok=True)

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="voice-tts", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=2)

    # -- public API --------------------------------------------------------
    def rate_limited(self, max_per_minute: int) -> bool:
        now = time.time()
        with self._lock:
            self._recent_times = [t for t in self._recent_times if now - t < 60]
            if len(self._recent_times) >= max_per_minute:
                return True
            self._recent_times.append(now)
            return False

    def announce(self, announcement: Announcement) -> Announcement:
        """Queue an announcement (non-blocking)."""
        with self._lock:
            self._history.append(announcement)
            del self._history[:-MAX_HISTORY]
        try:
            self._queue.put_nowait(announcement)
        except queue.Full:
            announcement.error = "announcement queue full"
            log.warning("Dropping announcement: queue full")
        return announcement

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._as_dict(a) for a in reversed(self._history)]

    def status(self) -> dict[str, Any]:
        with self._lock:
            last = self._history[-1] if self._history else None
            return {
                "engine": self.engine.as_dict(),
                "outputs": ["dashboard", "server", "both"],
                "last_announcement": self._as_dict(last) if last else None,
                "queue_size": self._queue.qsize(),
            }

    def _as_dict(self, a: Announcement) -> dict[str, Any]:
        return {
            "text": a.text,
            "camera_id": a.camera_id,
            "camera_name": a.camera_name,
            "event_id": a.event_id,
            "category": a.category,
            "output": a.output,
            "created_at": a.created_at,
            "spoken": a.spoken,
            "audio_url": a.audio_url,
            "error": a.error,
        }

    # -- worker ------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            try:
                self._process(item)
            except Exception as exc:  # noqa: BLE001 - never kill the thread
                item.error = str(exc)[:300]
                log.warning("TTS processing failed: %s", redact(str(exc)))

    def _process(self, item: Announcement) -> None:
        # Dashboard (browser) path: just publish; the browser speaks.
        if item.output in ("dashboard", "both"):
            broadcaster.publish_threadsafe(
                {
                    "topic": "voice",
                    "type": "announce",
                    "text": item.text,
                    "camera_id": item.camera_id,
                    "camera_name": item.camera_name,
                    "event_id": item.event_id,
                    "category": item.category,
                    "created_at": item.created_at,
                }
            )
            item.spoken = True

        if item.output in ("server", "both"):
            if not self.engine.can_synthesize:
                item.error = self.engine.detail
                broadcaster.publish_threadsafe(
                    {
                        "topic": "voice",
                        "type": "announce_failed",
                        "reason": self.engine.detail,
                        "camera_id": item.camera_id,
                    }
                )
                return
            audio_path = self._synthesize(item)
            if audio_path is not None:
                item.audio_url = f"/api/voice/clip/{audio_path.name}"
                item.spoken = True
                self._play(audio_path)
        broadcaster.publish_threadsafe(
            {"topic": "voice", "type": "announcement_state", "announcement": self._as_dict(item)}
        )

    def _synthesize(self, item: Announcement) -> Path | None:
        name = f"alert_{int(item.created_at)}_{abs(hash(item.text)) % 10000}.wav"
        out = self._clips_dir / name
        try:
            if self.engine.engine == "espeak":
                exe = _which("espeak-ng", "espeak")
                assert exe
                rate = int(settings_store_value("voice_rate", 175))
                subprocess.run(
                    [exe, "-v", "en", "-s", str(rate), "-w", str(out), item.text],
                    check=True, capture_output=True, timeout=20,
                )
                return out
            if self.engine.engine == "say":
                subprocess.run(["say", "-o", str(out), item.text], check=True, capture_output=True, timeout=30)
                return out
            if self.engine.engine == "pyttsx3":
                import pyttsx3

                engine = pyttsx3.init()
                engine.setProperty("rate", int(settings_store_value("voice_rate", 175)))
                engine.save_to_file(item.text, str(out))
                engine.runAndWait()
                return out if out.exists() else None
        except Exception as exc:  # noqa: BLE001
            item.error = str(exc)[:300]
            log.warning("TTS synthesis failed: %s", exc)
        return None

    def _play(self, path: Path) -> None:
        if not self.engine.can_play:
            return
        player = _which("aplay", "ffplay", "afplay", "play")
        if not player:
            return
        try:
            if player.endswith("ffplay"):
                cmd = [player, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
            else:
                cmd = [player, str(path)]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:  # noqa: BLE001
            log.warning("Audio playback failed: %s", exc)


def settings_store_value(key: str, default: Any) -> Any:
    # Imported lazily to avoid a circular import at module load time.
    from app.services.settings_store import settings_store

    try:
        return settings_store.get(key, default)
    except Exception:  # noqa: BLE001
        return default


voice_service = VoiceService()
