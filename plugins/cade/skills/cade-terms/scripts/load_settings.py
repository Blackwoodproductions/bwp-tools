"""Locate and load the cade-terms-merge settings file.

Walks upward from CWD looking for `.claude/skills.settings.{env}.json`,
returns the `cade-terms-merge` block. Same mechanics as the sibling
`cade-db-queries` loader, with two differences:

- Accepts the long env aliases (`production`, `staging`) alongside the short
  file suffixes (`prod`, `stg`); files on disk always use the short form.
- `local` is allowed to have NO settings block: the script then runs against
  the repo's own `.env` (the local dev database), which is the natural local
  mode. Every other env fails fast when its block is missing — never a
  silent fallback to another environment's database.

Settings files are gitignored via `.claude/*`, so credentials never reach
the repo.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_ALIASES = {
    "prod": "prod",
    "production": "prod",
    "stg": "stg",
    "staging": "stg",
    "stage": "stg",
    "local": "local",
    "dev": "local",
}
DEFAULT_ENV = "prod"
SKILL_KEY = "cade-terms-merge"


def resolve_env(env: str | None) -> str:
    raw = env or os.environ.get("CADE_SKILL_ENV") or DEFAULT_ENV
    resolved = _ALIASES.get(raw.lower())
    if resolved is None:
        raise ValueError(
            f"Invalid env '{raw}'. Valid: {', '.join(sorted(set(_ALIASES)))}."
        )
    return resolved


def _find_settings_file(env: str) -> Path | None:
    name = f".claude/skills.settings.{env}.json"
    current = Path.cwd().resolve()
    for candidate in [current, *current.parents]:
        path = candidate / name
        if path.is_file():
            return path
    return None


def load(env: str | None = None) -> tuple[str, dict[str, Any]]:
    """Return (resolved_env, settings_block).

    The block may be empty ONLY for `local` (repo `.env` fallback).
    """
    resolved = resolve_env(env)
    path = _find_settings_file(resolved)
    block: dict[str, Any] = {}
    if path is not None:
        block = json.loads(path.read_text()).get(SKILL_KEY, {})
    if not block and resolved != "local":
        raise SystemExit(
            f"No '{SKILL_KEY}' block found for env '{resolved}' "
            f"(.claude/skills.settings.{resolved}.json). Refusing to fall back "
            "to another environment. Add the block (see SKILL.md) or use "
            "--env local."
        )
    return resolved, block
