"""Whisper-based voice note transcription pipeline for DropInvoice."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from pydub import AudioSegment

logger = logging.getLogger("dropinvoice.processing.audio")

MIN_AUDIO_DURATION_SECONDS = 3.0
DEFAULT_WHISPER_MODEL = "base"


class ShortAudioError(Exception):
    """Raised when a voice note is too short for reliable transcription."""


class AudioProcessingError(Exception):
    """Raised when audio conversion or transcription fails."""


def process_audio(audio_path: str | Path, metadata: dict[str, Any] | None = None) -> str:
    """Convert a WhatsApp audio file to WAV, run Whisper, and return transcript."""

    path = Path(audio_path)
    audio = load_audio(path)
    validate_audio_duration(audio, metadata or {"audio_path": str(path)})
    wav_path = convert_audio_to_wav(audio, path)
    transcript = transcribe_audio(wav_path)
    cleaned_transcript = clean_transcript(transcript)

    if not cleaned_transcript:
        raise AudioProcessingError("Whisper returned an empty transcript.")

    logger.info("Transcribed %s characters from %s", len(cleaned_transcript), path)
    return cleaned_transcript


def load_audio(audio_path: Path) -> AudioSegment:
    """Load an OGG, MP3, WAV, or WhatsApp voice note using pydub."""

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file does not exist: {audio_path}")

    try:
        audio_segment = get_audio_segment_class()
        return audio_segment.from_file(str(audio_path))
    except Exception as exc:
        raise AudioProcessingError(f"Could not decode audio file: {audio_path}") from exc


def get_audio_segment_class() -> Any:
    """Return pydub's AudioSegment class or raise a clear dependency error."""

    try:
        from pydub import AudioSegment
    except ImportError as exc:
        raise AudioProcessingError("pydub is not installed.") from exc

    return AudioSegment


def validate_audio_duration(audio: AudioSegment, metadata: dict[str, Any]) -> None:
    """Raise ShortAudioError when the audio duration is under three seconds."""

    duration_seconds = len(audio) / 1000.0
    if duration_seconds < MIN_AUDIO_DURATION_SECONDS:
        message_sid = metadata.get("message_sid") or metadata.get("audio_path")
        logger.warning("Rejected short audio %.2fs for %s", duration_seconds, message_sid)
        raise ShortAudioError("Voice note too short. Please describe your items clearly.")


def convert_audio_to_wav(audio: AudioSegment, source_path: Path) -> Path:
    """Convert a pydub audio segment to mono 16 kHz WAV for Whisper."""

    output_dir = Path(tempfile.gettempdir()) / "dropinvoice" / "audio"
    output_dir.mkdir(parents=True, exist_ok=True)

    wav_path = output_dir / f"{source_path.stem}.wav"
    normalized_audio = audio.set_channels(1).set_frame_rate(16000)

    try:
        normalized_audio.export(str(wav_path), format="wav")
    except Exception as exc:
        raise AudioProcessingError(f"Could not export WAV file: {wav_path}") from exc

    return wav_path


def transcribe_audio(wav_path: Path) -> str:
    """Transcribe a WAV file with the configured local Whisper model."""

    model = load_whisper_model(get_whisper_model_name())

    try:
        result = model.transcribe(str(wav_path), fp16=False)
    except Exception as exc:
        raise AudioProcessingError(f"Whisper transcription failed: {wav_path}") from exc

    return str(result.get("text", ""))


@lru_cache(maxsize=2)
def load_whisper_model(model_name: str) -> Any:
    """Load and cache a local Whisper model by name."""

    try:
        import whisper
    except ImportError as exc:
        raise AudioProcessingError("openai-whisper is not installed.") from exc

    logger.info("Loading Whisper model: %s", model_name)
    return whisper.load_model(model_name)


def get_whisper_model_name() -> str:
    """Return the configured Whisper model name, defaulting to base."""

    return os.getenv("WHISPER_MODEL", DEFAULT_WHISPER_MODEL).strip() or DEFAULT_WHISPER_MODEL


def clean_transcript(transcript: str) -> str:
    """Normalize Whisper transcript whitespace for parser input."""

    compacted = re.sub(r"[ \t\r\f\v]+", " ", transcript)
    compacted = re.sub(r"\n{3,}", "\n\n", compacted)
    return compacted.strip()
