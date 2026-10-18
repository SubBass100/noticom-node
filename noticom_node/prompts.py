"""Spoken prompts, pre-rendered once with Piper for a natural voice.

`noticom-node render-prompts` (run by install.sh) synthesizes every prompt
to a WAV in the prompt cache. At runtime `hardware.speak()` plays the cached
file if there is one and falls back to espeak-ng otherwise, so a failed or
skipped Piper install never breaks anything.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import logging
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

from . import reset

_LOGGER = logging.getLogger(__name__)

DEFAULT_PROMPT_DIR = Path("/var/cache/noticom/prompts")

# Every sentence the node can say. Add new prompts here so they get rendered.
PROMPT_TEXTS = [reset.PROMPT, reset.CANCELLED]

TARGET_PEAK = 0.89


def prompt_dir() -> Path:
    """Where rendered prompts live (override with NOTICOM_PROMPT_DIR)."""
    return Path(os.environ.get("NOTICOM_PROMPT_DIR", DEFAULT_PROMPT_DIR))


def prompt_path(text: str, directory: Path | None = None) -> Path:
    """The file a given sentence renders to (keyed by its text)."""
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    return (directory or prompt_dir()) / f"{digest}.wav"


def cached(text: str) -> Path | None:
    """The rendered prompt for `text`, if it exists."""
    path = prompt_path(text)
    return path if path.exists() else None


def _normalize_wav(data: bytes) -> bytes:
    """Peak-normalize a 16-bit WAV so prompts are as loud as chimes."""
    with wave.open(io.BytesIO(data)) as wav:
        params = wav.getparams()
        samples = np.frombuffer(wav.readframes(params.nframes), "<i2").astype(np.float32)
    peak = float(np.abs(samples).max()) if samples.size else 0.0
    if peak > 0:
        samples = samples * (TARGET_PEAK * 32767 / peak)
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(params.nchannels)
        wav.setsampwidth(2)
        wav.setframerate(params.framerate)
        wav.writeframes(np.clip(samples, -32767, 32767).astype("<i2").tobytes())
    return out.getvalue()


def render(piper_python: str, model: str, directory: Path | None = None) -> int:
    """Render every prompt with Piper. Returns how many failed."""
    directory = directory or prompt_dir()
    directory.mkdir(parents=True, exist_ok=True)
    failures = 0
    for text in PROMPT_TEXTS:
        target = prompt_path(text, directory)
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "out.wav"
            # piper-tts reads the text from stdin (it takes no text argument).
            proc = subprocess.run(
                [piper_python, "-m", "piper", "-m", model, "-f", str(raw)],
                input=text.encode(),
                capture_output=True,
                timeout=300,
                check=False,
            )
            if proc.returncode != 0 or not raw.exists():
                failures += 1
                _LOGGER.warning(
                    "Piper couldn't render %r: %s",
                    text, proc.stderr.decode(errors="replace").strip()[-500:],
                )
                continue
            tmp_target = target.with_suffix(".tmp")
            tmp_target.write_bytes(_normalize_wav(raw.read_bytes()))
            tmp_target.replace(target)
        _LOGGER.info("Rendered prompt: %s", text)
    # Drop renders of prompts that no longer exist.
    keep = {prompt_path(t, directory).name for t in PROMPT_TEXTS}
    for path in directory.glob("*.wav"):
        if path.name not in keep:
            path.unlink(missing_ok=True)
    return failures


def main(argv: list[str]) -> int:
    """`noticom-node render-prompts --piper PYTHON --model VOICE.onnx`."""
    parser = argparse.ArgumentParser(prog="noticom-node render-prompts")
    parser.add_argument("--piper", required=True, help="Python interpreter that has piper-tts")
    parser.add_argument("--model", required=True, help="Path to the Piper voice .onnx file")
    parser.add_argument("--dir", type=Path, default=None, help="Output directory")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    failures = render(args.piper, args.model, args.dir)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
