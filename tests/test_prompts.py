"""Rendering prompts with Piper (faked) and playing them back."""

import stat
import sys
import wave

from noticom_node import prompts


def fake_piper(tmp_path, fail=False):
    """A stand-in for piper's Python: `python -m piper -m MODEL -f OUT`, text on stdin."""
    script = tmp_path / "fake-python"
    script.write_text(f"""#!{sys.executable}
import sys, wave, math, struct
args = sys.argv[1:]
assert args[:2] == ["-m", "piper"], args
if {fail!r}:
    sys.exit("model not found")
out = args[args.index("-f") + 1]
assert "--" not in args, "piper takes the text on stdin"
text = sys.stdin.read()
assert text.strip(), "no text on stdin"
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
    w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(i / 5))) for i in range(len(text) * 100)))
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_render_creates_loud_prompts(tmp_path):
    out = tmp_path / "prompts"
    (out).mkdir()
    (out / "stale0000000000.wav").write_bytes(b"old")
    assert prompts.render(fake_piper(tmp_path), "voice.onnx", out) == 0
    for text in prompts.PROMPT_TEXTS:
        path = prompts.prompt_path(text, out)
        with wave.open(str(path)) as w:
            frames = w.readframes(w.getnframes())
        peak = max(abs(int.from_bytes(frames[i:i + 2], "little", signed=True)) for i in range(0, len(frames), 2))
        assert peak > 0.85 * 32767  # normalized up from the quiet fake voice
    assert not (out / "stale0000000000.wav").exists()


def test_render_failure_reported(tmp_path):
    assert prompts.render(fake_piper(tmp_path, fail=True), "voice.onnx", tmp_path / "p") == len(prompts.PROMPT_TEXTS)


def test_cached_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTICOM_PROMPT_DIR", str(tmp_path))
    text = prompts.PROMPT_TEXTS[0]
    assert prompts.cached(text) is None
    prompts.render(fake_piper(tmp_path), "voice.onnx", tmp_path)
    assert prompts.cached(text) == prompts.prompt_path(text, tmp_path)


async def test_speak_prefers_rendered_prompt(tmp_path, monkeypatch):
    from noticom_node import hardware

    monkeypatch.setenv("NOTICOM_PROMPT_DIR", str(tmp_path))
    monkeypatch.setattr(hardware, "FAKE", False)
    played = []

    async def fake_play(path, device=None):
        played.append(path)

    monkeypatch.setattr(hardware, "play_file", fake_play)
    prompts.render(fake_piper(tmp_path), "voice.onnx", tmp_path)
    await hardware.speak(prompts.PROMPT_TEXTS[1])
    assert played == [prompts.prompt_path(prompts.PROMPT_TEXTS[1], tmp_path)]


def test_cli(tmp_path):
    import subprocess

    out = tmp_path / "cli"
    proc = subprocess.run(
        ["noticom-node", "render-prompts", "--piper", fake_piper(tmp_path),
         "--model", "voice.onnx", "--dir", str(out)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert len(list(out.glob("*.wav"))) == len(prompts.PROMPT_TEXTS)
