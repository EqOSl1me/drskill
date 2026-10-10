"""Shared test helpers."""

from __future__ import annotations

import json
from pathlib import Path


def grant_pi_trust(home: Path, proj: Path) -> None:
    """Grant pi project trust for `proj` so its project-native skill dirs
    (and project packages) load. pi defaults to untrusted when a project has
    trust-requiring resources and trust.json has no true entry."""
    agent = home / ".pi" / "agent"
    agent.mkdir(parents=True, exist_ok=True)
    (agent / "trust.json").write_text(
        json.dumps({str(proj.resolve()): True}), encoding="utf-8"
    )
