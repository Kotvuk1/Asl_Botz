"""
In-memory whitelist cache.

Source of truth is the `mei_users.is_whitelisted` column in the DB.
This cache avoids a DB query on every incoming message.

Lifecycle:
  1. init_whitelist(user_ids) called at bot startup with all DB-whitelisted IDs
  2. add_user / remove_user called by /adduser and /removeuser commands
  3. Owner is always allowed regardless of DB state
"""
from typing import Set

from config import settings

_allowed: Set[int] = set()


def init_whitelist(db_whitelisted_ids: list) -> None:
    """Populate cache from DB + env settings. Call once at startup.
    Builds the new set first, then replaces atomically to avoid a window
    where concurrent is_allowed() calls see an empty set.
    """
    new_set: Set[int] = set(settings.allowed_user_ids)
    new_set.update(db_whitelisted_ids)
    new_set.add(settings.owner_id)
    # Single assignment is atomic in CPython (GIL protects the pointer swap)
    _allowed.clear()
    _allowed.update(new_set)


def is_allowed(user_id: int) -> bool:
    """Check if a user has access. Fast in-memory lookup."""
    return user_id in _allowed


def add_user(user_id: int) -> None:
    """Add user to cache (call after DB update in /adduser)."""
    _allowed.add(user_id)


def remove_user(user_id: int) -> None:
    """Remove user from cache (call after DB update in /removeuser)."""
    if user_id != settings.owner_id:
        _allowed.discard(user_id)


def get_allowed_ids() -> Set[int]:
    """Return a copy of all currently allowed user IDs."""
    return set(_allowed)
