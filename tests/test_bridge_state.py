"""Tests for ``BridgeState`` behavior that does not spawn agents."""

from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

import pytest

from mobile_computer_use.bridge import MOBILE_DEVICE_SECRET_BYTES, PROTOCOL, BridgeState, parse_args
from tests.conftest import bridge_namespace


def test_normalize_provider_aliases(isolated_config_dir) -> None:
    state = BridgeState(bridge_namespace(isolated_config_dir))
    assert state.normalize_provider("codex") == "codex"
    assert state.normalize_provider("openai_codex") == "codex"
    assert state.normalize_provider("cursor_agent") == "cursor"
    assert state.normalize_provider("CURSOR") == "cursor"
    with pytest.raises(ValueError, match="unsupported computer provider"):
        state.normalize_provider("unknown_agent")


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("https://peytontolbert.com", True),
        ("http://localhost:8797", True),
        ("http://127.0.0.1:3000", True),
        ("https://evil.example", False),
        ("", False),
    ],
)
def test_origin_allowed(isolated_config_dir, origin: str, expected: bool) -> None:
    state = BridgeState(bridge_namespace(isolated_config_dir))
    assert state.origin_allowed(origin) == expected


def test_origin_allowed_for_host_matches_host_header(isolated_config_dir) -> None:
    state = BridgeState(bridge_namespace(isolated_config_dir))
    assert state.origin_allowed_for_host("http://192.168.1.5:45731", "192.168.1.5:45731")


def test_workspace_allowed_stays_inside_configured_roots(tmp_path, isolated_config_dir) -> None:
    allowed = tmp_path / "workspace"
    allowed.mkdir()
    nested = allowed / "project"
    nested.mkdir()
    outside = tmp_path / "workspace-evil"
    outside.mkdir()
    state = BridgeState(bridge_namespace(isolated_config_dir, workspace=[str(allowed)]))

    assert state.workspace_allowed(str(allowed)) == allowed.resolve()
    assert state.workspace_allowed(str(nested)) == nested.resolve()
    assert state.workspace_is_allowed(nested)
    assert not state.workspace_is_allowed(outside)
    with pytest.raises(ValueError, match="workspace is not allowed"):
        state.workspace_allowed(str(outside))


def test_mobile_prompt_with_image_attachment_saves_inside_workspace(tmp_path, isolated_config_dir) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = BridgeState(bridge_namespace(isolated_config_dir, workspace=[str(workspace)]))
    image_data = b"\x89PNG\r\n\x1a\n"

    prompt = state.mobile_prompt_with_attachments(
        "Describe this.",
        [{
            "name": "../screen shot.png",
            "mime_type": "image/png",
            "data": base64.b64encode(image_data).decode("ascii"),
        }],
        workspace,
    )

    assert prompt.startswith("Describe this.")
    saved_line = next(line for line in prompt.splitlines() if line.startswith("- "))
    saved_path = Path(saved_line[2:])
    assert saved_path.is_file()
    assert saved_path.read_bytes() == image_data
    assert saved_path.is_relative_to(workspace.resolve())
    assert ".." not in saved_path.name


def test_mobile_prompt_rejects_unsupported_attachment_type(tmp_path, isolated_config_dir) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = BridgeState(bridge_namespace(isolated_config_dir, workspace=[str(workspace)]))

    with pytest.raises(ValueError, match="unsupported attachment type"):
        state.mobile_prompt_with_attachments(
            "",
            [{"name": "note.txt", "mime_type": "text/plain", "data": "SGVsbG8="}],
            workspace,
        )


def test_health_payload_shape(isolated_config_dir) -> None:
    state = BridgeState(bridge_namespace(isolated_config_dir))
    payload = state.health_payload()
    assert payload["status"] == "ok"
    assert payload["protocol"] == PROTOCOL
    assert "bridge_public_jwk" in payload
    assert isinstance(payload["providers"], list)


def test_handle_relay_health(isolated_config_dir) -> None:
    state = BridgeState(bridge_namespace(isolated_config_dir))
    out = state.handle_relay_request({"path": "/health", "origin": "", "body": {}})
    assert out["status_code"] == 200
    body = out["payload"]
    assert isinstance(body, dict)
    assert body.get("status") == "ok"


def test_mobile_approval_requires_bridge_token_before_prompt(isolated_config_dir) -> None:
    state = BridgeState(bridge_namespace(isolated_config_dir))
    device = {
        "device_id": "phone_test_device_123",
        "device_secret": "s" * MOBILE_DEVICE_SECRET_BYTES,
        "device_name": "Test Phone",
    }

    with pytest.raises(ValueError, match="mobile approval token is invalid"):
        state.approve_mobile_console("wrong-token", device=device, approval_code="123456")


def test_mobile_approval_requires_six_digit_code_before_prompt(isolated_config_dir) -> None:
    state = BridgeState(bridge_namespace(isolated_config_dir))
    device = {
        "device_id": "phone_test_device_123",
        "device_secret": "s" * MOBILE_DEVICE_SECRET_BYTES,
        "device_name": "Test Phone",
    }

    with pytest.raises(ValueError, match="mobile approval code is missing"):
        state.approve_mobile_console(state.mobile_token, device=device, approval_code="12345")


def test_mobile_approval_can_use_desktop_approval_handler(isolated_config_dir) -> None:
    state = BridgeState(bridge_namespace(isolated_config_dir))
    state.desktop_approval_handler = lambda _kind, details: details["approval_code"]
    device = {
        "device_id": "phone_test_device_123",
        "device_secret": "s" * MOBILE_DEVICE_SECRET_BYTES,
        "device_name": "Test Phone",
    }

    result = state.approve_mobile_console(state.mobile_token, "30d", device, "123456")

    assert result["status"] == "approved"
    assert result["code"] == "123456"
    assert len(state.mobile_grants) == 1


def test_mobile_approval_creates_and_reuses_grant(monkeypatch: pytest.MonkeyPatch, isolated_config_dir) -> None:
    state = BridgeState(bridge_namespace(isolated_config_dir))
    device = {
        "device_id": "phone_test_device_123",
        "device_secret": "s" * MOBILE_DEVICE_SECRET_BYTES,
        "device_name": "Test Phone",
    }

    class FakeTTY:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", FakeTTY())
    monkeypatch.setattr("builtins.input", lambda _prompt="": "123456")

    first = state.approve_mobile_console(state.mobile_token, "30d", device, "123456")
    assert first["status"] == "approved"
    assert first["code"] == "123456"
    assert len(state.mobile_grants) == 1

    reused = state.approve_mobile_console(state.mobile_token, "30d", device, "654321")
    assert reused["grant_id"] == first["grant_id"]
    assert reused["code"] == "reused"
    assert len(state.mobile_grants) == 1


def test_require_mobile_grant_rejects_wrong_device_secret(isolated_config_dir) -> None:
    state = BridgeState(bridge_namespace(isolated_config_dir))
    device_id = "phone_test_device_123"
    device_secret = "s" * MOBILE_DEVICE_SECRET_BYTES
    grant_id = "mobile_test"
    state.mobile_grants[grant_id] = {
        "grant_id": grant_id,
        "created_at": time.time(),
        "expires_at": time.time() + 60,
        "duration": "30d",
        "device_id": device_id,
        "device_name": "Test Phone",
        "device_secret_hash": "not-the-right-hash",
    }

    with pytest.raises(ValueError, match="mobile device identity did not match approval"):
        state.require_mobile_grant({
            "grant_id": grant_id,
            "device_id": device_id,
            "device_secret": device_secret,
        })


def test_parse_args_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["mobile-computer-use-bridge"])
    ns = parse_args()
    assert ns.host == "127.0.0.1"
    assert ns.port == 45731
    assert ns.sandbox == "danger-full-access"


def test_parse_args_port_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["mobile-computer-use-bridge", "--port", "40000"])
    ns = parse_args()
    assert ns.port == 40000
