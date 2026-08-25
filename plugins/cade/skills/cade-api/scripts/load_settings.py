"""Locate and load the cade-api settings file.

Walks upward from CWD looking for `.claude/skills.settings.{env}.json`,
returns the `cade-api` block (a flat dict with `api-url`, `api-key`,
`wp-plugin-api-key`).

Environment selection mirrors the rest of the cade-* skills:

- Default env is `prod`. Credentials resolve from
  `.claude/skills.settings.prod.json`.
- Pass `env="stg"` / `env="local"` to `load()`, or export `CADE_SKILL_ENV`
  to target another environment for a batch of calls.
- Valid envs: `prod`, `stg`, `local`. Anything else raises `ValueError`.

Settings files are gitignored via `.claude/*`, so credentials never leave
the developer machine. Sibling skills (`cade-db-queries`, `cade-logfire`,
`cade-flower`) keep their own top-level keys in the same file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


VALID_ENVS: tuple[str, ...] = ("prod", "stg", "local")
DEFAULT_ENV = "prod"
SKILL_KEY = "cade-api"


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


def base_host(api_url: str | None) -> str:
    """Normalize `api-url` (from settings) to a bare host, no path suffix.

    Users paste the value in different shapes — `https://host`,
    `https://host/`, or `https://host/api/v1`. We strip any trailing
    `/api/v1`, `/api`, or trailing slash so the rest of the codebase can
    assume a clean host. Empty input returns empty — the caller decides
    the default.
    """
    if not api_url:
        return ""
    url = api_url.strip().rstrip("/")
    for suffix in ("/api/v1", "/api"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url.rstrip("/")
