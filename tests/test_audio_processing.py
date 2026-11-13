from __future__ import annotations

import io
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from audio_processing import (
    AudioProcessingError,
    SpeechWindow,
    _pad_wav_16bit_mono,
    _speech_flag_boundaries,
    convert_m4a_to_wav,
)


def test_vad_bounds_ignore_single_clicks():
    flags = [False] * 6 + [True] + [False] * 10 + [True, True, True] + [False] * 20
    assert _speech_flag_boundaries(flags, 0.02) == pytest.approx((0.34, 0.40))


def test_silence_has_no_detected_speech():
    assert _speech_flag_boundaries([False] * 50, 0.02) is None
    assert _speech_flag_boundaries([True], 0.02) is None


def test_sample_exact_padding(tmp_path: Path):
    sound = tmp_path / "source.wav"
    with wave.open(str(sound), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes((b"\x01\x02") * 4_800)
    padded, duration = _pad_wav_16bit_mono(sound)
    assert duration == pytest.approx(2.1, abs=0.00001)
    with wave.open(io.BytesIO(padded), "rb") as result:
        assert result.getframerate() == 48000
        assert result.getnchannels() == 1
        assert result.getsampwidth() == 2
        assert result.getnframes() == 100_800
        pcm = result.readframes(result.getnframes())
    assert pcm[:96000] == b"\0" * 96000
    assert pcm[96000:96008] == b"\x01\x02" * 4
    assert pcm[-96000:] == b"\0" * 96000


def test_missing_file():
    with pytest.raises(AudioProcessingError, match="nonempty"):
        convert_m4a_to_wav(b"")


def test_invalid_recording_rejected():
    if not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    with pytest.raises(AudioProcessingError):
        convert_m4a_to_wav(b"not-an-m4a")


@pytest.mark.skipif(not all(shutil.which(binary) for binary in ["ffmpeg", "espeak"]),
                    reason="FFmpeg + espeak needed for integration test")
def test_end_to_end_with_synthetic_spoken_voice(tmp_path: Path):
    # Generate spoken words instead of a sine tone, so WebRTC VAD sees actual speech.
    voice = tmp_path / "words.wav"
    subprocess.run(["espeak", "-w", str(voice), "Hello. This is a recording of my human voice."],
                   check=True, capture_output=True)
    recording = tmp_path / "test.m4a"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-i", str(voice),
        "-filter_complex", "[1:a]adelay=1500:all=1[a];[0:a][a]amix=inputs=2:duration=longest:normalize=0,atrim=duration=5[out]",
        "-map", "[out]", "-c:a", "aac", str(recording),
    ], check=True, capture_output=True)
    converted = convert_m4a_to_wav(recording.read_bytes())
    assert converted.speech_window.start_seconds > 0.5
    assert converted.speech_window.end_seconds > converted.speech_window.start_seconds
    with wave.open(io.BytesIO(converted.wav_bytes), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (48000, 1, 2)
        assert wav.getnframes() > 96000
        first = wav.readframes(48000)
        wav.setpos(wav.getnframes() - 48000)
        last = wav.readframes(48000)
        assert first == last == (b"\0" * 96000)
    assert converted.output_duration_seconds > 2.5


@pytest.mark.skipif(not all(shutil.which(binary) for binary in ["ffmpeg", "espeak"]),
                    reason="FFmpeg + espeak needed for integration test")
def test_manual_override(tmp_path: Path):
    voice = tmp_path / "words.wav"
    subprocess.run(["espeak", "-w", str(voice), "A good recording of human voice."],
                   check=True, capture_output=True)
    m4a = tmp_path / "input.m4a"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(voice), "-c:a", "aac", str(m4a)],
                   check=True, capture_output=True)
    content = m4a.read_bytes()
    with pytest.raises(AudioProcessingError, match="both manual|boundaries"):
        convert_m4a_to_wav(content, manual_start=9, manual_end=10)
    result = convert_m4a_to_wav(content, manual_start=0, manual_end=1)
    assert result.speech_window.detection == "manual"
    assert result.output_duration_seconds == pytest.approx(3, abs=0.03)
