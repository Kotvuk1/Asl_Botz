import logging
import sys
from datetime import datetime, timezone

from config import settings


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Reduce noise from third-party libs
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def current_datetime_str() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%d %B %Y, %H:%M UTC")


def truncate(text: str, max_len: int = 200) -> str:
    return text if len(text) <= max_len else text[:max_len] + "…"


def format_user_mention(user_id: int, first_name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{first_name}</a>'
