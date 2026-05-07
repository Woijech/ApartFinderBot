"""Persistent JSON storage for Telegram bot user profiles.

The storage layer is intentionally small and replaceable. If the bot outgrows a
single JSON file, keep the ``BotStorage`` public methods and swap the
implementation for SQLite/PostgreSQL.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from kufarpars.client import SearchRequest


@dataclass
class UserProfile:
    """A Telegram chat profile with search settings and seen listing ids."""

    chat_id: int
    enabled: bool = False
    request: SearchRequest = field(default_factory=SearchRequest)
    seen_ids: list[int] = field(default_factory=list)


class BotStorage:
    """Read and write bot profiles to a JSON file."""

    def __init__(self, path: str) -> None:
        """Create storage bound to ``path`` and load existing data."""
        self._path = Path(path)
        self._profiles: dict[int, UserProfile] = {}
        self.load()

    def load(self) -> None:
        """Load profiles from disk if the file exists."""
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding="utf-8"))
        profiles = data.get("profiles") or {}
        self._profiles = {
            int(chat_id): _profile_from_dict(item)
            for chat_id, item in profiles.items()
        }

    def save(self) -> None:
        """Persist all profiles to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "profiles": {
                str(chat_id): _profile_to_dict(profile)
                for chat_id, profile in self._profiles.items()
            }
        }
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, chat_id: int) -> UserProfile:
        """Return an existing profile or create a new default one."""
        if chat_id not in self._profiles:
            self._profiles[chat_id] = UserProfile(chat_id=chat_id)
            self.save()
        return self._profiles[chat_id]

    def update(self, profile: UserProfile) -> None:
        """Store one changed profile and persist it."""
        self._profiles[profile.chat_id] = profile
        self.save()

    def all_enabled(self) -> list[UserProfile]:
        """Return profiles with background monitoring enabled."""
        return [profile for profile in self._profiles.values() if profile.enabled]


def _profile_to_dict(profile: UserProfile) -> dict[str, object]:
    """Serialize a profile to JSON-compatible primitives."""
    data = asdict(profile)
    data["request"] = asdict(profile.request)
    return data


def _profile_from_dict(data: dict[str, object]) -> UserProfile:
    """Deserialize a profile from JSON-compatible primitives."""
    request_data = data.get("request") or {}
    if not isinstance(request_data, dict):
        request_data = {}
    return UserProfile(
        chat_id=int(data["chat_id"]),
        enabled=bool(data.get("enabled")),
        request=SearchRequest(**request_data),
        seen_ids=[int(item) for item in data.get("seen_ids", [])],
    )
