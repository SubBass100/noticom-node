"""Audio processing for PTT recordings.

The HAT records both mics in stereo. We average them to mono (the voice is
correlated across mics, hiss isn't, so this gains ~3 dB of SNR), remove DC
offset, then peak-normalize so every message plays at a consistent level.
"""

from __future__ import annotations

import io
import wave

import numpy as np

SAMPLE_RATE = 16000
TARGET_PEAK = 0.89  # about -1 dBFS
MAX_GAIN = 20.0  # +26 dB; stops near-silence being blown up into loud hiss
SILENCE_PEAK = 0.002  # below this, treat the clip as silent and leave it


def stereo_to_mono(pcm: bytes) -> np.ndarray:
    """Average interleaved 16-bit stereo PCM into float mono in [-1, 1]."""
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    if samples.size % 2:
        samples = samples[:-1]
    return samples.reshape(-1, 2).mean(axis=1)


def normalize(mono: np.ndarray) -> np.ndarray:
    """Remove DC offset and scale so the peak sits at TARGET_PEAK."""
    if mono.size == 0:
        return mono
    mono = mono - mono.mean()
    peak = float(np.abs(mono).max())
    if peak < SILENCE_PEAK:
        return mono
    return mono * min(TARGET_PEAK / peak, MAX_GAIN)


def to_wav(mono: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    """Encode float mono as 16-bit mono WAV bytes."""
    pcm = (np.clip(mono, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def process_recording(stereo_pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    """Raw stereo capture -> normalized mono WAV ready to upload."""
    return to_wav(normalize(stereo_to_mono(stereo_pcm)), rate)
