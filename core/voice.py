"""
Voice message transcription via Groq Whisper API.
Tries each available API key in order until one succeeds.
"""
import io
import logging

from groq import AsyncGroq, RateLimitError, AuthenticationError

from config import settings

logger = logging.getLogger(__name__)


async def transcribe_voice(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """
    Transcribe Telegram voice bytes to text using Groq whisper-large-v3.
    Rotates through all available API keys on rate-limit errors.
    Returns the transcribed text string. Raises RuntimeError if all keys fail.
    """
    keys = settings.groq_keys
    last_error: Exception = RuntimeError("No Groq API keys configured")

    for i, key in enumerate(keys):
        client = AsyncGroq(api_key=key)
        try:
            transcription = await client.audio.transcriptions.create(
                file=(filename, io.BytesIO(audio_bytes)),
                model="whisper-large-v3",
                language="ru",
                response_format="json",
            )
            text = getattr(transcription, "text", None) or str(transcription)
            return text.strip()
        except RateLimitError as e:
            logger.warning("Whisper key #%d rate limited, trying next: %s", i, e)
            last_error = e
        except AuthenticationError as e:
            logger.error("Whisper key #%d auth error: %s", i, e)
            last_error = e
        except Exception as e:
            # Non-rate-limit errors (network, bad audio) — don't retry other keys
            raise

    raise RuntimeError(
        f"All Groq API keys exhausted for voice transcription: {last_error}"
    )
