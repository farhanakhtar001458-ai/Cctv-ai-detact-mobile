import { useCallback, useEffect, useRef, useState } from "react";
import { useLive } from "./ws";
import type { VoiceMessage } from "./types";

const ENABLED_KEY = "scai.voice.enabled";
const RATE_KEY = "scai.voice.rate";
const VOLUME_KEY = "scai.voice.volume";

/**
 * Browser-side announcement channel.
 *
 * Browsers only allow audio after a user gesture, so the operator explicitly
 * enables announcements (once per browser). The backend decides *when* to alert
 * (cooldown, persistence, per-minute limits); this hook only plays what it is
 * told to play.
 */
export function useVoice() {
  const live = useLive();
  const [enabled, setEnabled] = useState<boolean>(() => localStorage.getItem(ENABLED_KEY) === "1");
  const [rate, setRate] = useState<number>(() => Number(localStorage.getItem(RATE_KEY) || 0.95));
  const [volume, setVolume] = useState<number>(() => Number(localStorage.getItem(VOLUME_KEY) || 0.85));
  const [lastSpoken, setLastSpoken] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [spokenCount, setSpokenCount] = useState(0);
  const enabledRef = useRef(enabled);
  const rateRef = useRef(rate);
  const volumeRef = useRef(volume);

  useEffect(() => {
    enabledRef.current = enabled;
    localStorage.setItem(ENABLED_KEY, enabled ? "1" : "0");
  }, [enabled]);
  useEffect(() => {
    rateRef.current = rate;
    localStorage.setItem(RATE_KEY, String(rate));
  }, [rate]);
  useEffect(() => {
    volumeRef.current = volume;
    localStorage.setItem(VOLUME_KEY, String(volume));
  }, [volume]);

  const speak = useCallback((message: VoiceMessage) => {
    if (!enabledRef.current) return;
    if (message.type === "announce" && message.text) {
      const synth = window.speechSynthesis;
      if (!synth) {
        setLastError("This browser does not support speech synthesis");
        return;
      }
      try {
        // Cancel the previous utterance so alerts never queue up for minutes.
        synth.cancel();
        const utterance = new SpeechSynthesisUtterance(message.text);
        utterance.rate = Math.min(2, Math.max(0.5, rateRef.current));
        utterance.volume = Math.min(1, Math.max(0, volumeRef.current));
        utterance.lang = "en";
        utterance.onend = () => setLastSpoken(message.text || null);
        utterance.onerror = (event) => setLastError(`Speech error: ${event.error}`);
        synth.speak(utterance);
        setLastSpoken(message.text);
        setSpokenCount((count) => count + 1);
        setLastError(null);
      } catch (error) {
        setLastError(error instanceof Error ? error.message : "Speech failed");
      }
      return;
    }
    if (message.type === "announcement_state" && message.announcement?.audio_url) {
      // Server-side TTS clip: play it so the monitoring PC reproduces the alert.
      const url = `${message.announcement.audio_url}?token=${encodeURIComponent(
        localStorage.getItem("scai.token") || "",
      )}`;
      const audio = new Audio(url);
      audio.volume = Math.min(1, Math.max(0, volumeRef.current));
      audio.play().catch(() => setLastError("Audio playback was blocked by the browser"));
    }
    if (message.type === "announce_failed" && message.reason) {
      setLastError(message.reason);
    }
  }, []);

  useEffect(() => live.subscribeVoice(speak), [live.subscribeVoice, speak]);

  const test = useCallback(() => {
    const synth = window.speechSynthesis;
    if (!synth) {
      setLastError("Speech synthesis unavailable in this browser");
      return;
    }
    const utterance = new SpeechSynthesisUtterance(
      "Mobile phone detected. Please follow classroom rules. This is a test announcement.",
    );
    utterance.rate = Math.min(2, Math.max(0.5, rateRef.current));
    utterance.volume = Math.min(1, Math.max(0, volumeRef.current));
    synth.speak(utterance);
  }, []);

  return {
    enabled,
    setEnabled,
    rate,
    setRate,
    volume,
    setVolume,
    test,
    lastSpoken,
    lastError,
    spokenCount,
    supported: typeof window !== "undefined" && "speechSynthesis" in window,
  };
}
