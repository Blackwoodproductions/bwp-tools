"""Locate and load the cade-db-queries settings file.

Walks upward from CWD looking for `.claude/skills.settings.{env}.json`,
returns the `cade-db-queries` block (a flat dict of db connection fields).

Environment selection (see SKILL.md → "Environment selection"):

- Default env is `prod`. When the caller does not specify an env, credentials
  are loaded from `.claude/skills.settings.prod.json`.
- Override by passing `env="stg"` / `env="local"` to `load()`, or by exporting
  `CADE_SKILL_ENV` before running the wrapper.
- Valid envs: `prod`, `stg`, `local`. Anything else raises `ValueError`.

Settings files are gitignored via `.claude/*`, so credentials never reach
the repo. Sibling skills (`cade-logfire`, `cade-flower`, ...) live under
their own top-level keys in the same file and are not read by this loader.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


VALID_ENVS: tuple[str, ...] = ("prod", "stg", "local")
DEFAULT_ENV = "prod"
SKILL_KEY = "cade-db-queries"


def _resolve_env(env: str | None) -> str:
    raw = env or os.environ.get("CADE_SKILL_ENV") or DEFAULT_ENV
    resolved = raw.lower()
    if resolved not in VALID_ENVS:
        raise ValueError(
            f"Invalid env '{raw}'. Valid envs: {', '.join(VALID_ENVS)}."
        )
    return resolved


def _settings_relative(env: str) -> Path:
    return Path(".claude") / f"skills.settings.{env}.json"


def _find_settings_file(start: Path, env: str) -> Path:
    relative = _settings_relative(env)
    for candidate in [start, *start.parents]:
        path = candidate / relative
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"Could not find {relative} walking up from {start}. "
        f"Run this from inside the cade-service repo, or create "
        f".claude/skills.settings.{env}.json containing a '{SKILL_KEY}' block."
    )


def load(start: Path | None = None, env: str | None = None) -> dict[str, Any]:
    env = _resolve_env(env)
    start = (start or Path.cwd()).resolve()
    path = _find_settings_file(start, env)
    data = json.loads(path.read_text())
    if SKILL_KEY not in data:
        raise KeyError(f"{path} is missing the '{SKILL_KEY}' block")
    return data[SKILL_KEY]
