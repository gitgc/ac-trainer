"""Text-to-speech backends and a priority queue that only talks when there's time to."""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .config import VoiceConfig

INFO, FEEDBACK, CUE, URGENT = 0, 1, 2, 3


class PrintBackend:
    """No audio: the manager already prints every line."""
    words_per_second = 3.0

    def speak(self, text: str, should_stop: Callable[[], bool]) -> None:
        pass


class SapiBackend:
    """Windows SAPI 5 voices (offline, low latency). Needs pywin32."""
    ASYNC, PURGE = 1, 2

    def __init__(self, cfg: VoiceConfig):
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        self.voice = win32com.client.Dispatch("SAPI.SpVoice")
        if cfg.voice:
            for token in self.voice.GetVoices():
                if cfg.voice.lower() in token.GetDescription().lower():
                    self.voice.Voice = token
                    break
            else:
                print(f"[voice] no voice matching '{cfg.voice}', using the default")
        if cfg.audio_device:
            for token in self.voice.GetAudioOutputs():
                if cfg.audio_device.lower() in token.GetDescription().lower():
                    self.voice.AudioOutput = token
                    break
            else:
                print(f"[voice] no audio output matching '{cfg.audio_device}', using the default")
        self.voice.Rate = max(-10, min(10, cfg.rate))
        self.voice.Volume = max(0, min(100, cfg.volume))
        self.words_per_second = 2.6 * 1.08 ** cfg.rate

    def speak(self, text: str, should_stop: Callable[[], bool]) -> None:
        self.voice.Speak(text, self.ASYNC | self.PURGE)
        while not self.voice.WaitUntilDone(40):
            if should_stop():
                self.voice.Speak("", self.ASYNC | self.PURGE)
                return


class CommandBackend:
    """macOS `say` or Linux `espeak` - handy for trying the demo away from the sim PC."""

    def __init__(self, cfg: VoiceConfig, program: str):
        wpm = int(175 * 1.08 ** cfg.rate)
        self.words_per_second = wpm / 60.0
        if program == "say":
            self.cmd = ["say", "-r", str(wpm)]
            if cfg.voice:
                self.cmd += ["-v", cfg.voice]
            if cfg.audio_device:
                self.cmd += ["-a", cfg.audio_device]
        else:
            self.cmd = [program, "-s", str(wpm), "-a", str(int(cfg.volume * 2))]

    def speak(self, text: str, should_stop: Callable[[], bool]) -> None:
        proc = subprocess.Popen(self.cmd + [text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while proc.poll() is None:
            if should_stop():
                proc.terminate()
                return
            time.sleep(0.04)


def make_backend(cfg: VoiceConfig):
    kind = cfg.backend
    if kind == "auto":
        if sys.platform == "win32":
            kind = "sapi"
        elif sys.platform == "darwin":
            kind = "say"
        else:
            kind = "espeak" if (shutil.which("espeak-ng") or shutil.which("espeak")) else "print"
    if kind == "sapi":
        try:
            return SapiBackend(cfg)
        except ImportError:
            print("[voice] pywin32 isn't installed (pip install pywin32) - printing instead of speaking")
            return PrintBackend()
    if kind == "say":
        return CommandBackend(cfg, "say")
    if kind == "espeak":
        return CommandBackend(cfg, shutil.which("espeak-ng") and "espeak-ng" or "espeak")
    return PrintBackend()


def list_voices() -> None:
    if sys.platform == "win32":
        import win32com.client

        v = win32com.client.Dispatch("SAPI.SpVoice")
        print("Voices (use part of the name as voice = \"...\"):")
        for token in v.GetVoices():
            print("   ", token.GetDescription())
        print("Audio outputs (use part of the name as audio_device = \"...\"):")
        for token in v.GetAudioOutputs():
            print("   ", token.GetDescription())
    elif sys.platform == "darwin":
        subprocess.run(["say", "-v", "?"])
        print("Audio devices:")
        subprocess.run(["say", "-a", "?"])
    else:
        print("Voices come from espeak; see `espeak --voices`.")


class TextSpeech:
    """Drop-in for SpeechManager that just collects (and optionally prints) the lines."""

    def __init__(self, log: Callable[[str], None] | None = print):
        self.lines: list[tuple[int, str]] = []
        self._log = log
        self.gate = None

    def say(self, text: str, priority: int = INFO, key: str | None = None, ttl: float = 10.0,
            gated: bool = True) -> None:
        self.lines.append((priority, text))
        if self._log:
            self._log(f'  >> "{text}"')

    def cancel(self, prefix: str) -> None:
        pass

    def duration(self, text: str) -> float:
        return 0.3 + len(text.split()) / 2.8


@dataclass
class Utterance:
    text: str
    priority: int = INFO
    key: str | None = None
    ttl: float = 10.0            # seconds it may wait for a quiet moment
    gated: bool = True           # wait until the driver isn't busy
    created: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created > self.ttl


class SpeechManager:
    """Single speaking thread. Higher priority first; a corner cue interrupts small talk.

    gate(duration_s, priority) is supplied by the coach and says whether the driver has
    `duration_s` of calm track ahead (not braking, not about to brake).
    """

    def __init__(self, backend_factory: Callable[[], object], log: Callable[[str], None] = print):
        self._factory = backend_factory
        self._log = log
        self._queue: list[Utterance] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._current: Utterance | None = None
        self._interrupt = False
        self._running = False
        self._thread: threading.Thread | None = None
        self.gate: Callable[[float, int], bool] = lambda duration, priority: True
        self.words_per_second = 2.8
        self.ready = threading.Event()

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, name="speech", daemon=True)
        self._thread.start()
        self.ready.wait(10)

    def stop(self, flush: float = 0.0) -> None:
        deadline = time.monotonic() + flush
        while flush and time.monotonic() < deadline and (self._queue or self._current):
            time.sleep(0.05)
        self._running = False
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2)

    def duration(self, text: str) -> float:
        return 0.3 + len(text.split()) / self.words_per_second

    def say(self, text: str, priority: int = INFO, key: str | None = None, ttl: float = 10.0,
            gated: bool = True) -> None:
        utt = Utterance(text, priority, key, ttl, gated)
        with self._lock:
            if key is not None:
                self._queue = [u for u in self._queue if u.key != key]
                if self._current is not None and self._current.key == key:
                    return
            self._queue.append(utt)
            if len(self._queue) > 8:
                self._queue.sort(key=lambda u: (-u.priority, u.created))
                self._queue = self._queue[:8]
            cur = self._current
            if cur is not None and priority >= CUE > cur.priority:
                self._interrupt = True
        self._wake.set()

    def cancel(self, prefix: str) -> None:
        with self._lock:
            self._queue = [u for u in self._queue if not (u.key or "").startswith(prefix)]

    def busy(self) -> bool:
        return self._current is not None or bool(self._queue)

    def _next(self) -> Utterance | None:
        with self._lock:
            self._queue = [u for u in self._queue if not u.expired]
            for u in sorted(self._queue, key=lambda u: (-u.priority, u.created)):
                if not u.gated or self.gate(self.duration(u.text), u.priority):
                    self._queue.remove(u)
                    self._current = u
                    self._interrupt = False
                    return u
        return None

    def _run(self) -> None:
        try:
            backend = self._factory()
        except Exception as exc:  # noqa: BLE001 - keep coaching in text if audio fails
            print(f"[voice] {exc} - printing instead of speaking")
            backend = PrintBackend()
        self.words_per_second = getattr(backend, "words_per_second", 2.8)
        self.ready.set()
        while self._running:
            utt = self._next()
            if utt is None:
                self._wake.wait(0.05)
                self._wake.clear()
                continue
            self._log(f'  >> "{utt.text}"')
            try:
                backend.speak(utt.text, lambda: self._interrupt or not self._running)
            except Exception as exc:  # noqa: BLE001
                print(f"[voice] {exc}")
            with self._lock:
                self._current = None
