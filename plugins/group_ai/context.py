"""File-based session context manager for multi-turn conversations.

Persists conversation history to disk (one JSON file per group).
Sessions survive service restarts.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import get_settings

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages conversation history per (group, user) pair with file persistence.

    Stores one JSON file per group under the sessions directory.
    Files are named by group_openid for easy cleanup.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._lock = asyncio.Lock()
        # sessions_dir defaults to ./sessions relative to cwd
        self._sessions_dir = Path(os.environ.get("SESSIONS_DIR", "sessions"))
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        # In-memory cache: {group_openid: {user_openid: {"messages": [...], "last_active": ...}}}
        self._cache: dict[str, dict[str, dict]] = {}
        self._load_all()

    def _file_for_group(self, group_openid: str) -> Path:
        return self._sessions_dir / f"{group_openid}.json"

    def _load_all(self) -> None:
        """Load all session files from disk into memory cache."""
        if not self._sessions_dir.exists():
            return
        for f in self._sessions_dir.glob("*.json"):
            group_openid = f.stem
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                self._cache[group_openid] = data
            except Exception:
                logger.warning("Failed to load session file: %s", f)

    def _save_group(self, group_openid: str) -> None:
        """Write a group's sessions to disk."""
        data = self._cache.get(group_openid, {})
        if not data:
            return
        filepath = self._file_for_group(group_openid)
        filepath.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _key(self, group_openid: str, user_openid: str) -> str:
        return f"{group_openid}:{user_openid}"

    async def get_history(self, group_openid: str, user_openid: str) -> list[dict]:
        """Get conversation history for a user in a group."""
        # M6: timezone-aware UTC
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            group = self._cache.setdefault(group_openid, {})
            if user_openid not in group:
                group[user_openid] = {
                    "messages": [],
                    "last_active": now_iso,
                }
            else:
                group[user_openid]["last_active"] = now_iso
            return group[user_openid]["messages"]

    async def trim_history(self, group_openid: str, user_openid: str) -> None:
        """Trim history to max configured length and save to disk."""
        async with self._lock:
            group = self._cache.get(group_openid, {})
            if user_openid in group:
                msgs = group[user_openid]["messages"]
                max_len = self._settings.session_max_history
                if len(msgs) > max_len * 2:
                    group[user_openid]["messages"] = msgs[-max_len * 2 :]
                self._save_group(group_openid)

    async def clear_session(self, group_openid: str, user_openid: str) -> bool:
        """Clear a specific user's session in a group. Returns True if existed."""
        async with self._lock:
            group = self._cache.get(group_openid, {})
            if user_openid in group:
                del group[user_openid]
                # If group is now empty, delete the file entirely
                if not group:
                    self._delete_group_file(group_openid)
                    del self._cache[group_openid]
                else:
                    self._save_group(group_openid)
                logger.info("Cleared session for %s:%s", group_openid, user_openid)
                return True
            return False

    async def clear_group(self, group_openid: str) -> bool:
        """Clear all sessions for a group (delete the file)."""
        async with self._lock:
            if group_openid in self._cache:
                del self._cache[group_openid]
                self._delete_group_file(group_openid)
                logger.info("Cleared all sessions for group %s", group_openid)
                return True
            # Also try deleting file even if not in cache
            filepath = self._file_for_group(group_openid)
            if filepath.exists():
                filepath.unlink()
                return True
            return False

    def _delete_group_file(self, group_openid: str) -> None:
        """Delete the session file for a group."""
        filepath = self._file_for_group(group_openid)
        if filepath.exists():
            filepath.unlink()

    async def active_count(self) -> int:
        """Return the number of active (group, user) sessions."""
        async with self._lock:
            return sum(len(users) for users in self._cache.values())

    async def cleanup_expired(self) -> int:
        """Remove expired sessions. Returns count removed."""
        timeout = timedelta(minutes=self._settings.session_timeout)
        # M6: timezone-aware UTC
        now = datetime.now(timezone.utc)
        removed = 0
        async with self._lock:
            groups_to_delete = []
            for group_openid, users in self._cache.items():
                users_to_delete = []
                for user_openid, data in users.items():
                    try:
                        last = datetime.fromisoformat(data["last_active"])
                        # Existing files may have been written without tz info;
                        # treat naive values as UTC.
                        if last.tzinfo is None:
                            last = last.replace(tzinfo=timezone.utc)
                        if now - last > timeout:
                            users_to_delete.append(user_openid)
                    except (ValueError, KeyError):
                        pass
                for u in users_to_delete:
                    del users[u]
                    removed += 1
                if not users:
                    groups_to_delete.append(group_openid)
                else:
                    self._save_group(group_openid)
            for g in groups_to_delete:
                del self._cache[g]
                self._delete_group_file(g)
        if removed:
            logger.info("Cleaned up %d expired sessions", removed)
        return removed
