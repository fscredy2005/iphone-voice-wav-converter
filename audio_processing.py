"""Private, temporary-file-based voice recording conversion to 48 kHz mono WAV."""

from __future__ import annotations

import io
import json
import math
import re
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

import webrtcvad

TARGET_LUFS = -16.0
TARGET_TRUE_PEAK = -1.5
TARGET_LRA = 11
OUTPUT_SAMPLE_RATE = 48_000
OUTPUT_CHANNELS = 1
OUTPUT_SAMPLE_WIDTH = 2  # 16-bit PCM
PADDING_SECONDS = 1
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_DURATION_SECONDS = 15 * 60
VAD_SAMPLE_RATE = 16_000
VAD_FRAME_MS = 20


class AudioProcessingError(ValueError):
    """An audio file cannot be converted safely or correctly."""


@dataclass(frozen=True)
class SpeechWindow:
    start_seconds: float
    end_seconds: float
    source_duration_seconds: float
    detection: str


@dataclass(frozen=True)
class ConversionResult:
    wav_bytes: bytes
    speech_window: SpeechWindow
    output_duration_seconds: float
    loudness_target_lufs: float = TARGET_LUFS


def _require_tools() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise AudioProcessingError(
            "FFmpeg/ffprobe is missing. Install FFmpeg locally or add 'ffmpeg' to packages.txt on Streamlit Cloud."
        )


def _run(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess[bytes]:
    try:
        proc = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AudioProcessingError("Processing timed out. Try a shorter recording.") from exc
    except OSError as exc:
        raise AudioProcessingError("Unable to start FFmpeg.") from exc
    if proc.returncode:
        details = proc.stderr.decode("utf-8", errors="replace")[-1200:]
        raise AudioProcessingError(f"Could not process this audio file. {details}")
    return proc


def _duration_seconds(path: Path) -> float:
    proc = _run([
        "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
        "stream=duration:format=duration", "-of", "json", str(path),
    ], timeout=30)
    info = json.loads(proc.stdout)
    streams = info.get("streams") or []
    if not streams:
        raise AudioProcessingError("No audio track found in this file.")
    values = [streams[0].get("duration"), info.get("format", {}).get("duration")]
    for value in values:
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(seconds) and seconds > 0:
            return seconds
    raise AudioProcessingError("Could not determine recording duration.")


def _speech_flag_boundaries(flags: list[bool], frame_seconds: float) -> tuple[float, float] | None:
    """Ignore isolated VAD hits, retaining short real utterances and pauses between words."""
    if not flags or not any(flags):
        return None
    # A true speech event requires two positive frames within 100 ms.
    # This removes occasional false positive clicks while accepting short words.
    accepted = [
        i for i, positive in enumerate(flags)
        if positive and sum(flags[max(0, i - 2):min(len(flags), i + 4)]) >= 2
    ]
    if not accepted:
        return None
    return accepted[0] * frame_seconds, (accepted[-1] + 1) * frame_seconds


def _detect_speech_window(path: Path, duration: float) -> SpeechWindow:
    # Decode to the sample rate and signed 16-bit PCM required by WebRTC VAD.
    pcm = _run([
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(path), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(VAD_SAMPLE_RATE),
        "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1",
    ], timeout=120).stdout
    bytes_per_frame = VAD_SAMPLE_RATE * VAD_FRAME_MS // 1000 * 2
    vad = webrtcvad.Vad(1)  # Less aggressive: safeguard quiet consonants.
    flags = [
        vad.is_speech(pcm[start:start + bytes_per_frame], VAD_SAMPLE_RATE)
        for start in range(0, len(pcm) - bytes_per_frame + 1, bytes_per_frame)
    ]
    bounds = _speech_flag_boundaries(flags, VAD_FRAME_MS / 1000)
    if bounds is None:
        raise AudioProcessingError(
            "Speech could not be detected automatically. Choose Manual timing and specify the start and end of your speech."
        )
    first, last = bounds
    # Small safeguards for breaths and unvoiced consonants that VAD can miss.
    return SpeechWindow(
        start_seconds=max(0.0, first - 0.06),
        end_seconds=min(duration, last + 0.12),
        source_duration_seconds=duration,
        detection="automatic (WebRTC voice detection)",
    )


def _extract_loudnorm_json(stderr: bytes) -> dict[str, str]:
    text = stderr.decode("utf-8", errors="replace")
    candidates = re.findall(r"\{\s*\"input_i\".*?\}", text, flags=re.DOTALL)
    if not candidates:
        raise AudioProcessingError("Audio loudness could not be measured.")
    try:
        stats = json.loads(candidates[-1])
        for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
            if not math.isfinite(float(stats[key])):
                raise ValueError(key)
        return stats
    except (ValueError, KeyError, TypeError) as exc:
        raise AudioProcessingError("Loudness measurements are invalid; is the voice audible?") from exc


def _normalization_filters(window: SpeechWindow) -> str:
    return (
        f"aresample={OUTPUT_SAMPLE_RATE},aformat=channel_layouts=mono,"
        f"atrim=start={window.start_seconds:.6f}:end={window.end_seconds:.6f},"
        "asetpts=PTS-STARTPTS"
    )


def _pad_wav_16bit_mono(wav_path: Path) -> tuple[bytes, float]:
    """Use PCM zeros for sample-exact 1.000-second leading/trailing silence."""
    try:
        with wave.open(str(wav_path), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (
                OUTPUT_CHANNELS, OUTPUT_SAMPLE_WIDTH, OUTPUT_SAMPLE_RATE, "NONE"
            ):
                raise AudioProcessingError("FFmpeg produced the wrong WAV format.")
            audio_frames = source.readframes(source.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioProcessingError("Failed to read normalized WAV audio.") from exc
    if not audio_frames:
        raise AudioProcessingError("The selected speech segment has no audio.")
    pad = b"\x00" * OUTPUT_SAMPLE_RATE * OUTPUT_CHANNELS * OUTPUT_SAMPLE_WIDTH * PADDING_SECONDS
    result = io.BytesIO()
    with wave.open(result, "wb") as dest:
        dest.setnchannels(OUTPUT_CHANNELS)
        dest.setsampwidth(OUTPUT_SAMPLE_WIDTH)
        dest.setframerate(OUTPUT_SAMPLE_RATE)
        dest.writeframes(pad + audio_frames + pad)
    seconds = 2 * PADDING_SECONDS + len(audio_frames) / (OUTPUT_SAMPLE_RATE * OUTPUT_SAMPLE_WIDTH)
    return result.getvalue(), seconds


def convert_m4a_to_wav(
    content: bytes,
    *,
    manual_start: float | None = None,
    manual_end: float | None = None,
) -> ConversionResult:
    """Normalize voice to -16 LUFS / -1.5 dBTP and frame with exact PCM silence.

    Works in memory + a self-cleaning temporary directory: no recording is retained.
    Manual timing is provided if automatic voice activity detection is unreliable.
    """
    if not content:
        raise AudioProcessingError("Upload a nonempty .m4a recording.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise AudioProcessingError("File exceeds the 100 MB upload limit.")
    if (manual_start is None) != (manual_end is None):
        raise AudioProcessingError("Both manual start and end times are required.")
    _require_tools()
    with tempfile.TemporaryDirectory(prefix="voice_wav_") as directory:
        folder = Path(directory)
        source = folder / "recording.m4a"
        intermediate = folder / "normalized.wav"
        source.write_bytes(content)
        duration = _duration_seconds(source)
        if duration > MAX_DURATION_SECONDS:
            raise AudioProcessingError("Recording is longer than 15 minutes.")
        if manual_start is not None and manual_end is not None:
            start, end = float(manual_start), float(manual_end)
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration + 0.05):
                raise AudioProcessingError("Manual speech boundaries must fall inside the recording.")
            window = SpeechWindow(start, min(end, duration), duration, "manual")
        else:
            window = _detect_speech_window(source, duration)
        if window.end_seconds - window.start_seconds < 0.06:
            raise AudioProcessingError("Speech selection is too short (under 60 ms).")

        filters = _normalization_filters(window)
        first_pass = (
            f"{filters},loudnorm=I={TARGET_LUFS}:TP={TARGET_TRUE_PEAK}:"
            f"LRA={TARGET_LRA}:print_format=json"
        )
        base_args = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", str(source), "-map", "0:a:0", "-vn"]
        analysis = _run(base_args + ["-af", first_pass, "-f", "null", "-"], timeout=150)
        stats = _extract_loudnorm_json(analysis.stderr)
        measured = ":".join([
            f"measured_I={stats['input_i']}",
            f"measured_TP={stats['input_tp']}",
            f"measured_LRA={stats['input_lra']}",
            f"measured_thresh={stats['input_thresh']}",
            f"offset={stats['target_offset']}",
        ])
        second_pass = (
            f"{filters},loudnorm=I={TARGET_LUFS}:TP={TARGET_TRUE_PEAK}:"
            f"LRA={TARGET_LRA}:{measured}:linear=true,aresample={OUTPUT_SAMPLE_RATE}"
        )
        _run(base_args + ["-af", second_pass, "-ac", "1", "-ar", str(OUTPUT_SAMPLE_RATE),
                          "-c:a", "pcm_s16le", str(intermediate)], timeout=150)
        padded_bytes, output_seconds = _pad_wav_16bit_mono(intermediate)
        return ConversionResult(padded_bytes, window, output_seconds)
