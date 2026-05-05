"""Shared fixtures for bridge tests."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path

import pytest


def bridge_namespace(config_dir: Path | str, **overrides: object) -> argparse.Namespace:
    """Build an argparse.Namespace compatible with ``BridgeState``."""
    data: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 45731,
        "workspace": [],
        "config_dir": str(config_dir),
        "codex_bin": "codex",
        "claude_bin": "claude",
        "cursor_bin": "cursor-agent",
        "tmux_bin": "tmux",
        "timeout": 900,
        "sandbox": "danger-full-access",
        "approval_policy": "never",
        "allow_origin": [],
        "relay_url": "",
        "relay_public_url": "",
        "relay_ttl": 86400,
        "reset_trusted_devices": False,
        "no_auto_reload": False,
    }
    data.update(overrides)
    return argparse.Namespace(**data)


@pytest.fixture
def isolated_config_dir(tmp_path: Path) -> Iterator[Path]:
    """Per-test config directory (under tmp_path)."""
    d = tmp_path / "bridge_cfg"
    d.mkdir(parents=True, exist_ok=True)
    yield d
