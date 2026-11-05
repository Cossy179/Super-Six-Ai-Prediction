"""Locating and loading configuration files and runtime settings."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def project_root() -> Path:
    """Directory holding ``configs/``: ``$SUPER6_HOME``, the cwd or an ancestor, or the checkout."""
    env = os.environ.get("SUPER6_HOME")
    if env:
        return Path(env).resolve()
    for candidate in (Path.cwd(), *Path.cwd().parents):
        if (candidate / "configs" / "competition_rules.yaml").is_file():
            return candidate
    return _PACKAGE_ROOT


def config_dir() -> Path:
    return Path(os.environ.get("SUPER6_CONFIG_DIR", project_root() / "configs"))


def load_yaml(name: str | Path) -> dict[str, Any]:
    path = Path(name)
    if not path.is_absolute() and not path.exists():
        path = config_dir() / path
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return data


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_dir: Path
    reports_dir: Path

    @classmethod
    def from_env(cls) -> Settings:
        root = project_root()
        data_dir = Path(os.environ.get("SUPER6_DATA_DIR", root / "data"))
        default_db = f"sqlite:///{data_dir / 'super6.db'}"
        return cls(
            database_url=os.environ.get("SUPER6_DATABASE_URL", default_db),
            data_dir=data_dir,
            reports_dir=Path(os.environ.get("SUPER6_REPORTS_DIR", root / "reports")),
        )


@lru_cache(maxsize=1)
def code_version() -> str:
    """Package version plus git revision (with a ``-dirty`` marker) when available."""
    from super6 import __version__

    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=_PACKAGE_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_PACKAGE_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return __version__
    return f"{__version__}+g{sha}{'-dirty' if dirty else ''}"
