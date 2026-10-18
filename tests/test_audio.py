import io
import wave

import numpy as np

from noticom_node import audio, mixer


def stereo_pcm(left, right):
    inter = np.empty(left.size * 2, dtype=np.float32)
    inter[0::2], inter[1::2] = left, right
    return (inter * 32767).astype("<i2").tobytes()


def tone(n=1600, amp=0.05):
    return (amp * np.sin(np.linspace(0, 40 * np.pi, n))).astype(np.float32)


def test_stereo_to_mono_averages_channels():
    left, right = np.full(4, 0.5, np.float32), np.zeros(4, np.float32)
    mono = audio.stereo_to_mono(stereo_pcm(left, right))
    assert mono.shape == (4,)
    assert np.allclose(mono, 0.25, atol=1e-3)


def test_quiet_voice_is_normalized_to_target_peak():
    t = tone(amp=0.05)
    out = audio.normalize(audio.stereo_to_mono(stereo_pcm(t, t)))
    assert abs(np.abs(out).max() - audio.TARGET_PEAK) < 0.01


def test_gain_is_capped():
    t = tone(amp=0.01)  # would need 89x; cap is 20x
    out = audio.normalize(t)
    assert np.abs(out).max() < 0.25


def test_silence_left_alone():
    out = audio.normalize(np.zeros(100, np.float32))
    assert not out.any()


def test_dc_offset_removed():
    out = audio.normalize(tone() + 0.1)
    assert abs(out.mean()) < 1e-3


def test_process_recording_writes_16k_mono_wav():
    t = tone()
    data = audio.process_recording(stereo_pcm(t, t))
    with wave.open(io.BytesIO(data)) as wav:
        assert (wav.getnchannels(), wav.getframerate(), wav.getnframes()) == (1, 16000, t.size)


def test_mixer_settings_map_ha_values():
    settings = dict(mixer.build_settings(volume=150, mic_gain=60, mic_boost=9))
    assert settings["Speaker"] == ["100%"]
    assert settings["Capture"] == ["60%", "cap"]
    assert settings["Left Input Boost Mixer LINPUT1"] == ["3"]
    assert settings["DAC Mono Mix"] == ["Mono"]
