"""
Groq API router with smart rotation across up to 5 API keys.

Strategy:
  1. Try current key.
  2. On RateLimitError or AuthenticationError → rotate to next key.
  3. If all keys exhausted → raise final error.
  4. Tracks per-key failure counts and cooldowns (60s); resets on success.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import List

from groq import AsyncGroq, RateLimitError, AuthenticationError, APIError

from config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system_prompt.txt"
_SYSTEM_PROMPT_TEMPLATE: str = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


class GroqRouter:
    """Thread-safe async Groq client with key rotation."""

    def __init__(self, keys: List[str], model: str) -> None:
        self._keys = keys
        self._model = model
        self._current_index = 0
        self._failures = [0] * len(keys)
        self._last_error_time = [0.0] * len(keys)
        self._lock = asyncio.Lock()
        self._clients = [AsyncGroq(api_key=k) for k in keys]

    def _next_key_index(self, failed_index: int) -> int:
        """Round-robin to the next available key."""
        for offset in range(1, len(self._keys)):
            idx = (failed_index + offset) % len(self._keys)
            # Skip keys that failed recently (within 60s)
            if time.time() - self._last_error_time[idx] > 60:
                return idx
        # All keys in cooldown — try the least-recently failed
        return min(range(len(self._keys)), key=lambda i: self._last_error_time[i])

    async def chat(
        self,
        messages: List[dict],
        memory_context: str = "",
        current_datetime: str = "",
        tasks_context: str = "",
        goals_context: str = "",
        habits_context: str = "",
        think_mode: bool = False,
    ) -> str:
        system_content = _SYSTEM_PROMPT_TEMPLATE.format(
            memory_context=memory_context or "Нет сохранённых данных.",
            current_datetime=current_datetime,
        )

        if tasks_context:
            system_content += (
                "\n\n## Задачи пользователя (последние 30 дней):\n"
                + tasks_context
            )

        if goals_context:
            system_content += (
                "\n\n## Активные цели пользователя (используй ID для управления):\n"
                + goals_context
            )

        if habits_context:
            system_content += (
                "\n\n## Привычки пользователя (используй ID для habitdone):\n"
                + habits_context
            )

        if think_mode:
            system_content += (
                "\n\n## РЕЖИМ «ДУМАЕМ ВСЛУХ»:\n"
                "Пользователь хочет подумать вслух. Не давай готовых решений и советов. "
                "Задавай уточняющие вопросы по одному. Помогай ему самому прийти к выводу. "
                "Действуй как коуч: слушай, отражай, уточняй."
            )

        full_messages = [{"role": "system", "content": system_content}] + messages

        async with self._lock:
            start_index = self._current_index

        attempts = 0
        current_idx = start_index

        while attempts < len(self._keys):
            client = self._clients[current_idx]
            try:
                response = await client.chat.completions.create(
                    model=self._model,
                    messages=full_messages,
                    max_tokens=settings.groq_max_tokens,
                    temperature=settings.groq_temperature,
                )
                # Success — reset failure count, update current index
                async with self._lock:
                    self._failures[current_idx] = 0
                    self._current_index = current_idx

                return response.choices[0].message.content.strip()

            except RateLimitError:
                logger.warning("Groq key #%d rate limited, rotating.", current_idx)
                self._failures[current_idx] += 1
                self._last_error_time[current_idx] = time.time()
                current_idx = self._next_key_index(current_idx)
                attempts += 1
                await asyncio.sleep(1)

            except AuthenticationError:
                logger.error("Groq key #%d authentication failed.", current_idx)
                self._failures[current_idx] += 1
                self._last_error_time[current_idx] = time.time()
                current_idx = self._next_key_index(current_idx)
                attempts += 1

            except APIError as e:
                logger.error("Groq APIError on key #%d: %s", current_idx, e)
                raise

        raise RuntimeError(
            "All Groq API keys exhausted. Please wait a minute and try again."
        )

    async def summarize(self, messages: List[dict], system_prompt: str) -> str:
        """
        Light summarization call with key rotation (same as chat() but smaller tokens).
        """
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        async with self._lock:
            start_index = self._current_index

        attempts = 0
        current_idx = start_index

        while attempts < len(self._keys):
            client = self._clients[current_idx]
            try:
                response = await client.chat.completions.create(
                    model=self._model,
                    messages=full_messages,
                    max_tokens=400,
                    temperature=0.3,
                )
                async with self._lock:
                    self._failures[current_idx] = 0
                    self._current_index = current_idx
                return response.choices[0].message.content.strip()

            except RateLimitError:
                logger.warning("Groq key #%d rate limited (summarize), rotating.", current_idx)
                self._failures[current_idx] += 1
                self._last_error_time[current_idx] = time.time()
                current_idx = self._next_key_index(current_idx)
                attempts += 1
                await asyncio.sleep(1)

            except AuthenticationError:
                logger.error("Groq key #%d auth failed (summarize).", current_idx)
                self._failures[current_idx] += 1
                self._last_error_time[current_idx] = time.time()
                current_idx = self._next_key_index(current_idx)
                attempts += 1

            except APIError as e:
                logger.error("Groq APIError on key #%d (summarize): %s", current_idx, e)
                raise

        raise RuntimeError(
            "All Groq API keys exhausted. Please wait a minute and try again."
        )

    @property
    def key_statuses(self) -> List[dict]:
        return [
            {
                "index": i,
                "failures": self._failures[i],
                "in_cooldown": time.time() - self._last_error_time[i] < 60,
            }
            for i in range(len(self._keys))
        ]


# Singleton instance
groq_router = GroqRouter(keys=settings.groq_keys, model=settings.groq_model)
