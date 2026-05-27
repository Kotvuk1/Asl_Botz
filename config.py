from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import List
import os


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram
    bot_token: str
    owner_id: int

    # Whitelist (comma-separated telegram user IDs)
    whitelist_ids: str = ""

    # Groq API keys — at least 1 required, up to 5 for rotation
    groq_api_key_1: str
    groq_api_key_2: str = ""
    groq_api_key_3: str = ""
    groq_api_key_4: str = ""
    groq_api_key_5: str = ""

    # Groq model
    groq_model: str = "llama-3.3-70b-versatile"
    groq_max_tokens: int = 2048
    groq_temperature: float = 0.7

    # Database (Neon PostgreSQL)
    database_url: str

    # Bot settings
    max_history_messages: int = 20
    max_memory_items: int = 50
    bot_name: str = "Бакыт"
    bot_short_name: str = "Бак"

    # Timezone (UTC offset, e.g. 5 = UTC+5 Almaty)
    tz_offset: int = 5

    # Logging
    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def fix_postgres_url(cls, v: str) -> str:
        # SQLAlchemy 2.0 requires postgresql+asyncpg:// for async
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        # asyncpg doesn't accept sslmode= query param — remove it;
        # SSL is enabled via connect_args in the engine instead.
        if "sslmode=" in v:
            import re
            v = re.sub(r"[?&]sslmode=[^&]*", "", v)
            v = re.sub(r"\?$", "", v)
        return v

    @property
    def groq_keys(self) -> List[str]:
        # Return only non-empty keys so rotation works with 1–5 keys
        return [
            k for k in [
                self.groq_api_key_1,
                self.groq_api_key_2,
                self.groq_api_key_3,
                self.groq_api_key_4,
                self.groq_api_key_5,
            ] if k
        ]

    @property
    def allowed_user_ids(self) -> List[int]:
        ids = [self.owner_id]
        if self.whitelist_ids:
            for uid in self.whitelist_ids.split(","):
                uid = uid.strip()
                if uid.isdigit():
                    ids.append(int(uid))
        return list(set(ids))

    @property
    def sync_database_url(self) -> str:
        """Synchronous URL for Alembic migrations."""
        url = self.database_url
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "")
        return url

    @property
    def TABLE_PREFIX(self) -> str:
        return "bak_"


settings = Settings()
