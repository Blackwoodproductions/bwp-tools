"""Run: python3 test_call_api.py — the raw-key containment for minted API keys."""

import json
import os
import stat
import tempfile
from pathlib import Path

import call_api

minted = {"id": "k1", "label": "seo-service", "key": "key_svc_abc"}
masked, secret = call_api._split_secret(minted)
assert masked == {"id": "k1", "label": "seo-service", "key": "***"}, masked
assert secret == "key_svc_abc"
assert minted["key"] == "key_svc_abc", "the caller's body is not mutated"
for body in ({"id": "k1"}, [{"key": "key_x"}], "key_x", None, {"key": ""}):
    assert call_api._split_secret(body) == (body, None), body

with tempfile.TemporaryDirectory() as tmp:
    os.chdir(tmp)
    inputs = {"service": "seo", "method": "POST", "path": "/api/v1/internal/admin/api-keys"}
    log = call_api._save_run(inputs, {"body": masked}, secret=secret)
    key_file = log.with_suffix(".key")
    assert key_file.read_text() == "key_svc_abc", "no trailing newline: gh secret set stores it byte for byte"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert "key_svc_abc" not in log.read_text(), "never in the run log"
    assert not call_api._save_run({**inputs, "method": "GET"}, {"body": None}).with_suffix(".key").exists()

print("ok")
