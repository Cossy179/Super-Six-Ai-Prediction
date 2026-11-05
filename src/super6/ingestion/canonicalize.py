"""Team-name canonicalisation (DESIGN.md §8.5).

Canonical team IDs are slugs of the Football-Data.co.uk name (``"Nott'm Forest"`` →
``nottm-forest``). ``configs/team_aliases.yaml`` adds display names and aliases used by
other sources or typed by people (``"Nottingham Forest"``). Fuzzy matching is only ever
used to *suggest* a name in an error message, never to accept one.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from super6.config import load_yaml

_PUNCT = re.compile(r"[’'`.,()]")
_SPACE = re.compile(r"[\s_\-/]+")


class UnknownTeamError(KeyError):
    def __init__(self, name: str, suggestions: list[str]) -> None:
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        super().__init__(f"unknown team {name!r}.{hint}")
        self.name = name
        self.suggestions = suggestions


def normalise_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    text = _PUNCT.sub("", text)
    return _SPACE.sub(" ", text).strip()


def slugify(name: str) -> str:
    slug = normalise_name(name).replace(" ", "-")
    if not slug:
        raise ValueError(f"cannot build a team id from {name!r}")
    return slug


@dataclass
class TeamRegistry:
    """Maps any known alias to a canonical team ID."""

    display_names: dict[str, str] = field(default_factory=dict)
    _aliases: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(cls, path: str = "team_aliases.yaml") -> TeamRegistry:
        raw = load_yaml(path)
        reg = cls()
        teams: Mapping[str, Any] = raw.get("teams", {}) or {}
        for team_id, entry in teams.items():
            reg.add_team(team_id, entry.get("name", team_id), entry.get("aliases", []))
        return reg

    def add_team(self, team_id: str, display_name: str, aliases: Iterable[str] = ()) -> None:
        self.display_names.setdefault(team_id, display_name)
        for alias in (team_id, display_name, *aliases):
            key = normalise_name(alias)
            existing = self._aliases.get(key)
            if existing is not None and existing != team_id:
                raise ValueError(f"alias {alias!r} maps to both {existing} and {team_id}")
            self._aliases[key] = team_id

    def register_source_name(self, name: str) -> str:
        """Resolve a provider name, creating a canonical team when it is new."""
        key = normalise_name(name)
        if key in self._aliases:
            return self._aliases[key]
        team_id = slugify(name)
        self.add_team(team_id, name, [name])
        return team_id

    def resolve(self, name: str) -> str:
        """Strict lookup for user-supplied names: exact alias match or ``UnknownTeamError``."""
        key = normalise_name(name)
        if key in self._aliases:
            return self._aliases[key]
        suggestions = difflib.get_close_matches(key, list(self._aliases), n=3, cutoff=0.6)
        raise UnknownTeamError(name, sorted({self._aliases[s] for s in suggestions}))

    def name(self, team_id: str) -> str:
        return self.display_names.get(team_id, team_id)

    def aliases(self) -> dict[str, str]:
        return dict(self._aliases)
