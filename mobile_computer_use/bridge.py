#!/usr/bin/env python3
"""Local encrypted computer-use bridge for Agent Kernel Lite.

The bridge binds to 127.0.0.1 by default. Pairing uses a short code printed in
the terminal plus explicit local approval on the computer, then every
post-pairing message is encrypted with P-256 ECDH, HKDF-SHA256, and
AES-256-GCM. Use --host 0.0.0.0 only when pairing from another device on a
trusted LAN.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import queue
import secrets
import shutil
import sqlite3
import socket
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import request as urlrequest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except ImportError:
    print("Missing dependency: cryptography", file=sys.stderr)
    print("Install it with: python -m pip install cryptography", file=sys.stderr)
    raise


PROTOCOL = "agent-kernel-computer-bridge/v1"
LEGACY_PROTOCOL = "agent-kernel-codex-bridge/v1"
MAX_JSON_BODY_BYTES = 128 * 1024
MOBILE_PAGE_FILE = Path(__file__).resolve().parents[1] / "web" / "computer-use-mobile.html"
PAIRING_GRANT_TTL_SECONDS = 60 * 60 * 24 * 30
MOBILE_DEFAULT_GRANT_DURATION = "30d"
MOBILE_DEVICE_SECRET_BYTES = 32
BRIDGE_SOURCE_FILES = [
    Path(__file__).resolve(),
    Path(__file__).resolve().with_name("run_agentkernel_lite_computer_bridge.py"),
]
DEFAULT_ALLOWED_ORIGINS = {
    "https://peytontolbert.com",
    "capacitor://localhost",
    "ionic://localhost",
    "http://localhost:8797",
    "http://127.0.0.1:8797",
}
ALLOWED_SANDBOXES = {"danger-full-access", "read-only", "workspace-write"}
ALLOWED_APPROVAL_POLICIES = {"never", "on-request"}
PROVIDER_ALIASES = {
    "codex": "codex",
    "openai_codex": "codex",
    "claude": "claude_code",
    "claude_code": "claude_code",
    "cursor": "cursor",
    "cursor_agent": "cursor",
}


def b64url_uint(value: int) -> str:
    raw = value.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_to_int(value: str) -> int:
    padded = value + ("=" * (-len(value) % 4))
    return int.from_bytes(base64.urlsafe_b64decode(padded.encode("ascii")), "big")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(value: str) -> bytes:
    return base64.b64decode(str(value).encode("ascii"), validate=True)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def public_key_to_jwk(public_key: ec.EllipticCurvePublicKey) -> dict[str, str]:
    numbers = public_key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64url_uint(numbers.x),
        "y": b64url_uint(numbers.y),
        "ext": True,
    }


def public_key_from_jwk(jwk: dict[str, Any]) -> ec.EllipticCurvePublicKey:
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise ValueError("browser key must be an EC P-256 JWK")
    numbers = ec.EllipticCurvePublicNumbers(
        b64url_to_int(str(jwk["x"])),
        b64url_to_int(str(jwk["y"])),
        ec.SECP256R1(),
    )
    return numbers.public_key()


def load_or_create_private_key(path: Path) -> ec.EllipticCurvePrivateKey:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    private_key = ec.generate_private_key(ec.SECP256R1())
    path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return private_key


def request_origin(handler: BaseHTTPRequestHandler) -> str:
    return str(handler.headers.get("Origin") or "")


def add_bridge_cors_headers(handler: BaseHTTPRequestHandler) -> None:
    origin = request_origin(handler)
    if not handler.state.origin_allowed(origin):
        return
    handler.send_header("Access-Control-Allow-Origin", origin)
    handler.send_header("Vary", "Origin")
    handler.send_header("Access-Control-Allow-Headers", "content-type")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
    if str(handler.headers.get("Access-Control-Request-Private-Network") or "").lower() == "true":
        handler.send_header("Access-Control-Allow-Private-Network", "true")


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    add_bridge_cors_headers(handler)
    handler.end_headers()
    handler.wfile.write(raw)


def html_response(handler: BaseHTTPRequestHandler, status: int, html_text: str) -> None:
    raw = html_text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def options_response(handler: BaseHTTPRequestHandler, status: int = 204, payload: dict[str, Any] | None = None) -> None:
    raw = b"" if status == 204 else json.dumps(payload or {}, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    if status != 204:
        handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    add_bridge_cors_headers(handler)
    if handler.state.origin_allowed(request_origin(handler)):
        handler.send_header("Access-Control-Max-Age", "600")
    handler.end_headers()
    if raw:
        handler.wfile.write(raw)


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        return {}
    if length > MAX_JSON_BODY_BYTES:
        raise ValueError("request body is too large")
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def post_json(url: str, payload: dict[str, Any], timeout: int = 35) -> tuple[int, dict[str, Any]]:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return int(response.status), json.loads(body) if body else {}
    except HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            payload = json.loads(body) if body else {}
        except Exception:
            payload = {"status": "error", "error": body or str(error)}
        return int(error.code), payload


def local_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        hostname = socket.gethostname()
        for item in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = item[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        address = probe.getsockname()[0]
        probe.close()
        if address and not address.startswith("127."):
            addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


class BridgeState:
    def __init__(self, args: argparse.Namespace) -> None:
        self.host = str(args.host or "127.0.0.1")
        self.port = int(args.port)
        self.config_dir = Path(args.config_dir).expanduser()
        self.private_key = load_or_create_private_key(self.config_dir / "bridge-device-key.pem")
        self.grants_path = self.config_dir / "pairing-grants.json"
        self.mobile_grants_path = self.config_dir / "mobile-grants.json"
        self.session_index_path = self.config_dir / "computer-use-sessions.json"
        self.allowed_workspaces = [Path(item).expanduser().resolve() for item in args.workspace]
        if not self.allowed_workspaces:
            self.allowed_workspaces = [Path.cwd().resolve()]
        self.provider_bins = {
            "codex": shutil.which(args.codex_bin) or args.codex_bin,
            "claude_code": shutil.which(args.claude_bin) or args.claude_bin,
            "cursor": shutil.which(args.cursor_bin) or args.cursor_bin,
            "tmux": shutil.which(args.tmux_bin) or args.tmux_bin,
        }
        self.timeout = int(args.timeout)
        self.sandbox = str(args.sandbox)
        self.approval_policy = str(args.approval_policy)
        self.allowed_origins = set(DEFAULT_ALLOWED_ORIGINS)
        for origin in args.allow_origin:
            self.allowed_origins.add(str(origin).rstrip("/"))
        self.mobile_token = secrets.token_urlsafe(24)
        self.mobile_grants = self.load_mobile_grants()
        self.pairing: dict[str, dict[str, Any]] = {}
        self.pairing_approval_lock = threading.Lock()
        self.grants = self.load_grants()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.sessions_lock = threading.Lock()
        if self.sandbox not in ALLOWED_SANDBOXES:
            raise ValueError(f"unsupported sandbox: {self.sandbox}")
        if self.approval_policy not in ALLOWED_APPROVAL_POLICIES:
            raise ValueError(f"unsupported approval policy: {self.approval_policy}")
        self.restore_session_index()

    def health_payload(self) -> dict[str, Any]:
        providers = self.provider_catalog()
        codex = next((provider for provider in providers if provider["id"] == "codex"), {})
        return {
            "status": "ok",
            "protocol": PROTOCOL,
            "legacy_protocol": LEGACY_PROTOCOL,
            "paired": bool(self.grants),
            "bridge_public_jwk": public_key_to_jwk(self.private_key.public_key()),
            "providers": providers,
            "codex_bin": codex.get("binary", ""),
            "codex_available": bool(codex.get("available")),
            "allowed_workspaces": [str(item) for item in self.allowed_workspaces],
            "workspace_policy": "selected workspace must equal an allowed root or be inside it",
            "sandbox": self.sandbox,
            "approval_policy": self.approval_policy,
        }

    def start_pairing_request(self, origin: str, body: dict[str, Any]) -> dict[str, Any]:
        origin = self.require_allowed_origin(origin)
        self.cleanup_pairing_requests()
        browser_public_jwk = body.get("browser_public_jwk")
        public_key_from_jwk(browser_public_jwk)
        pairing_id = f"pair_{secrets.token_urlsafe(12)}"
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = time.time() + 300
        self.pairing[pairing_id] = {
            "pairing_id": pairing_id,
            "code": code,
            "origin": origin,
            "browser_public_jwk": browser_public_jwk,
            "expires_at": expires_at,
            "attempts": 0,
        }
        print("", flush=True)
        print("Agent Kernel Lite computer-use pairing request", flush=True)
        print(f"Origin: {origin}", flush=True)
        print(f"Pairing code: {code}", flush=True)
        print(f"Browser key fingerprint: {self.pairing_fingerprint(self.pairing[pairing_id])}", flush=True)
        print("Enter this code in the Agent Kernel Lite app within 5 minutes.", flush=True)
        print("The computer must also approve the pairing after the code is entered.", flush=True)
        print("", flush=True)
        return {
            "status": "pairing_code_required",
            "pairing_id": pairing_id,
            "protocol": PROTOCOL,
            "origin": origin,
            "bridge_public_jwk": public_key_to_jwk(self.private_key.public_key()),
            "expires_at": expires_at,
            "code_length": 6,
        }

    def confirm_pairing_request(self, origin: str, body: dict[str, Any]) -> dict[str, Any]:
        origin = self.require_allowed_origin(origin)
        pairing_id = str(body.get("pairing_id") or "")
        code = str(body.get("code") or "").strip()
        request = self.pairing.get(pairing_id)
        if not request or float(request["expires_at"]) < time.time():
            raise ValueError("pairing request expired or missing")
        if origin != request.get("origin"):
            raise ValueError("pairing origin does not match request origin")
        request["attempts"] = int(request.get("attempts") or 0) + 1
        if int(request["attempts"]) > 5:
            self.pairing.pop(pairing_id, None)
            raise ValueError("too many pairing attempts")
        if not secrets.compare_digest(code, str(request["code"])):
            raise ValueError("pairing code did not match")
        self.approve_pairing_on_computer(request)
        grant_id = f"grant_{secrets.token_urlsafe(18)}"
        grant = {
            "grant_id": grant_id,
            "origin": request["origin"],
            "browser_public_jwk": request["browser_public_jwk"],
            "created_at": time.time(),
            "expires_at": time.time() + PAIRING_GRANT_TTL_SECONDS,
            "last_seq": 0,
        }
        self.grants[grant_id] = grant
        self.pairing.pop(pairing_id, None)
        self.save_grants()
        return {"status": "paired", "grant_id": grant_id, "expires_at": grant["expires_at"]}

    def encrypted_message_response(self, origin: str, envelope: dict[str, Any]) -> dict[str, Any]:
        origin = self.require_allowed_origin(origin)
        if envelope.get("protocol") not in {PROTOCOL, LEGACY_PROTOCOL}:
            raise ValueError("unsupported protocol")
        seq = int(envelope.get("seq") or 0)
        payload, grant = self.decrypt_message(envelope)
        if origin != grant.get("origin"):
            raise ValueError("request origin does not match pairing grant")
        message_type = str(payload.get("type") or "")
        if message_type in {"computer.session.start", "codex.session.start"}:
            result = self.start_codex_session(payload)
        elif message_type in {"computer.session.send", "codex.session.send"}:
            result = self.send_codex_followup(payload)
        elif message_type in {"computer.session.status", "codex.session.status"}:
            session_id = str(payload.get("session_id") or "")
            result = self.session_snapshot(session_id, int(payload.get("since") or 0)) if session_id else {
                "status": "ok",
                "message": "bridge is ready",
                "providers": self.provider_catalog(),
                "external_sessions": self.discover_external_codex_sessions(),
                "codex_chats": self.recent_codex_chats(),
                "tmux_panes": self.list_tmux_panes(),
                "active_sessions": [
                    self.session_snapshot(session_id, 0)
                    for session_id in list(self.sessions.keys())
                    if self.sessions.get(session_id, {}).get("status") == "running"
                ],
            }
        elif message_type in {"computer.session.import", "codex.session.import"}:
            result = self.import_codex_session(payload)
        elif message_type in {"computer.session.discover", "codex.session.discover"}:
            result = {
                "status": "ok",
                "external_sessions": self.discover_external_codex_sessions(),
                "codex_chats": self.recent_codex_chats(),
                "tmux_panes": self.list_tmux_panes(),
            }
        elif message_type in {"computer.tmux.attach", "codex.tmux.attach"}:
            result = self.attach_tmux_pane(payload)
        elif message_type in {"computer.session.rename", "codex.session.rename"}:
            result = self.rename_session(payload)
        elif message_type in {"computer.session.cancel", "codex.session.cancel"}:
            result = self.cancel_codex_session(payload)
        elif message_type in {"computer.session.close", "codex.session.close"}:
            result = self.close_codex_session(payload)
        elif message_type in {"computer.diff.read", "codex.diff.read"}:
            result = self.read_diff(payload)
        elif message_type in {"computer.grant.revoke", "codex.grant.revoke"}:
            removed = self.grants.pop(str(grant["grant_id"]), None)
            self.save_grants()
            result = {"status": "revoked" if removed else "missing", "grant_id": grant["grant_id"]}
        else:
            raise ValueError(f"unsupported encrypted message type: {message_type}")
        return self.encrypt_response(grant, seq, {"type": f"{message_type}.result", "result": result})

    def handle_relay_request(self, request: dict[str, Any]) -> dict[str, Any]:
        path = str(request.get("path") or "")
        origin = str(request.get("origin") or "").rstrip("/")
        body = request.get("body") if isinstance(request.get("body"), dict) else {}
        try:
            if path == "/health":
                return {"status_code": 200, "payload": self.health_payload()}
            if path == "/pairing/start":
                return {"status_code": 200, "payload": self.start_pairing_request(origin, body)}
            if path == "/pairing/confirm":
                return {"status_code": 200, "payload": self.confirm_pairing_request(origin, body)}
            if path == "/v1/message":
                return {"status_code": 200, "payload": self.encrypted_message_response(origin, body)}
            raise ValueError("not found")
        except Exception as exc:
            return {"status_code": 400, "payload": {"status": "error", "error": str(exc)}}

    def normalize_provider(self, provider: str) -> str:
        normalized = PROVIDER_ALIASES.get(str(provider or "codex").strip().lower().replace("-", "_"), "")
        if not normalized:
            raise ValueError(f"unsupported computer provider: {provider}")
        return normalized

    def provider_bin(self, provider: str) -> str:
        provider = self.normalize_provider(provider)
        binary = str(self.provider_bins.get(provider) or "")
        if not (shutil.which(binary) or Path(binary).exists()):
            raise ValueError(f"{provider} provider binary is not available: {binary}")
        return binary

    def provider_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "codex",
                "name": "Codex",
                "available": bool(shutil.which(str(self.provider_bins["codex"])) or Path(str(self.provider_bins["codex"])).exists()),
                "binary": str(self.provider_bins["codex"]),
                "capabilities": ["session.start", "session.send", "session.status", "session.cancel", "session.close", "session.import", "session.discover", "diff.read"],
            },
            {
                "id": "tmux",
                "name": "tmux",
                "available": bool(shutil.which(str(self.provider_bins["tmux"])) or Path(str(self.provider_bins["tmux"])).exists()),
                "binary": str(self.provider_bins["tmux"]),
                "capabilities": ["tmux.list", "tmux.attach", "session.send", "session.status", "session.close"],
            },
            {
                "id": "claude_code",
                "name": "Claude Code",
                "available": bool(shutil.which(str(self.provider_bins["claude_code"])) or Path(str(self.provider_bins["claude_code"])).exists()),
                "binary": str(self.provider_bins["claude_code"]),
                "capabilities": ["session.start"],
                "status": "adapter placeholder; enable after provider command contract is validated",
            },
            {
                "id": "cursor",
                "name": "Cursor",
                "available": bool(shutil.which(str(self.provider_bins["cursor"])) or Path(str(self.provider_bins["cursor"])).exists()),
                "binary": str(self.provider_bins["cursor"]),
                "capabilities": ["session.start", "session.send", "session.status", "session.cancel", "session.close", "diff.read"],
            },
        ]

    def origin_allowed(self, origin: str) -> bool:
        if not origin:
            return False
        origin = origin.rstrip("/")
        if origin in self.allowed_origins:
            return True
        return origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:")

    def origin_allowed_for_host(self, origin: str, host_header: str) -> bool:
        if self.origin_allowed(origin):
            return True
        host = str(host_header or "").strip()
        if not origin or not host:
            return False
        origin = origin.rstrip("/")
        return origin in {f"http://{host}", f"https://{host}"}

    def require_allowed_origin(self, origin: str) -> str:
        origin = origin.rstrip("/")
        if not self.origin_allowed(origin):
            allowed = ", ".join(sorted(self.allowed_origins))
            raise ValueError(f"origin is not allowed: {origin or 'missing'} (allowed: {allowed}, plus localhost loopback ports)")
        return origin

    def load_grants(self) -> dict[str, dict[str, Any]]:
        if not self.grants_path.exists():
            return {}
        try:
            value = json.loads(self.grants_path.read_text())
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def save_grants(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.grants_path.write_text(json.dumps(self.grants, indent=2, sort_keys=True))
        try:
            self.grants_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def load_mobile_grants(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self.mobile_grants_path.read_text())
        except Exception:
            return {}
        now = time.time()
        out: dict[str, dict[str, Any]] = {}
        if not isinstance(data, dict):
            return out
        for grant_id, grant in data.items():
            if not isinstance(grant, dict):
                continue
            expires_at = float(grant.get("expires_at") or 0)
            if expires_at and expires_at < now:
                continue
            out[str(grant_id)] = grant
        return out

    def save_mobile_grants(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.mobile_grants_path.write_text(json.dumps(self.mobile_grants, indent=2, sort_keys=True))
        try:
            self.mobile_grants_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def reset_trusted_devices(self) -> dict[str, int]:
        browser_count = len(self.grants)
        mobile_count = len(self.mobile_grants)
        self.grants = {}
        self.mobile_grants = {}
        self.save_grants()
        self.save_mobile_grants()
        return {"browser_pairings": browser_count, "mobile_devices": mobile_count}

    def cleanup_pairing_requests(self) -> None:
        now = time.time()
        self.pairing = {
            pairing_id: request
            for pairing_id, request in self.pairing.items()
            if float(request.get("expires_at") or 0) >= now
        }

    def pairing_fingerprint(self, request: dict[str, Any]) -> str:
        raw = json.dumps(request.get("browser_public_jwk") or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return ":".join(digest[index:index + 2] for index in range(0, 16, 2))

    def approve_pairing_on_computer(self, request: dict[str, Any]) -> None:
        if not sys.stdin.isatty():
            raise ValueError("local computer approval is required, but this bridge has no interactive terminal")
        with self.pairing_approval_lock:
            print("", flush=True)
            print("Approve Agent Kernel computer-use pairing?", flush=True)
            print(f"Origin: {request.get('origin')}", flush=True)
            print(f"Pairing code: {request.get('code')}", flush=True)
            print(f"Browser key fingerprint: {self.pairing_fingerprint(request)}", flush=True)
            print("Type APPROVE to complete pairing, or anything else to reject.", flush=True)
            answer = input("> ").strip()
        if answer != "APPROVE":
            raise ValueError("pairing was rejected on the computer")

    @staticmethod
    def mobile_grant_ttl_seconds(duration: str) -> int:
        value = str(duration or "").strip().lower().replace("_", "-")
        if value in {"month", "30d", "30-day", "30-days"}:
            return 60 * 60 * 24 * 30
        if value in {"week", "7d", "7-day", "7-days"}:
            return 60 * 60 * 24 * 7
        if value in {"day", "24h", "24-hour", "24-hours"}:
            return 60 * 60 * 24
        if value in {"forever", "unlimited", "never"}:
            return 0
        return 60 * 60 * 24 * 30

    def approve_mobile_console(self, token: str, duration: str = MOBILE_DEFAULT_GRANT_DURATION, device: dict[str, Any] | None = None) -> dict[str, Any]:
        device = device if isinstance(device, dict) else {}
        device_id = str(device.get("device_id") or "").strip()
        device_secret = str(device.get("device_secret") or "").strip()
        device_name = str(device.get("device_name") or "Phone").strip()[:120] or "Phone"
        if not device_id.startswith("phone_") or len(device_id) < 18:
            raise ValueError("mobile device identity is missing")
        if len(device_secret) < MOBILE_DEVICE_SECRET_BYTES:
            raise ValueError("mobile device secret is missing")
        existing = self.find_mobile_grant_by_device(device_id, device_secret)
        requested_duration = str(duration or MOBILE_DEFAULT_GRANT_DURATION)
        if existing and str(existing.get("duration") or MOBILE_DEFAULT_GRANT_DURATION) == requested_duration:
            self.renew_mobile_grant(existing)
            return {
                "status": "approved",
                "grant_id": existing["grant_id"],
                "device_id": device_id,
                "code": "reused",
                "expires_at": existing["expires_at"],
                "duration": existing["duration"],
            }
        if not sys.stdin.isatty():
            raise ValueError("local computer approval is required, but this bridge has no interactive terminal")
        grant_id = f"mobile_{secrets.token_urlsafe(18)}"
        code = f"{secrets.randbelow(1_000_000):06d}"
        ttl = self.mobile_grant_ttl_seconds(duration)
        expires_at = time.time() + ttl if ttl else 0
        device_secret_hash = sha256_hex(device_secret)
        with self.pairing_approval_lock:
            print("", flush=True)
            print("Approve Agent Kernel mobile console?", flush=True)
            print(f"Mobile code: {code}", flush=True)
            print(f"Device: {device_name}", flush=True)
            print(f"Device id: {device_id}", flush=True)
            print(f"Device fingerprint: {device_secret_hash[:16]}", flush=True)
            print(f"Requested duration: {'unlimited' if not ttl else duration}", flush=True)
            print("Type APPROVE to allow this phone to use the local Computer Use console.", flush=True)
            answer = input("> ").strip()
        if answer != "APPROVE":
            raise ValueError("mobile console was rejected on the computer")
        grant = {
            "grant_id": grant_id,
            "created_at": time.time(),
            "expires_at": expires_at,
            "duration": str(duration or MOBILE_DEFAULT_GRANT_DURATION),
            "device_id": device_id,
            "device_name": device_name,
            "device_secret_hash": device_secret_hash,
        }
        self.mobile_grants[grant_id] = grant
        self.save_mobile_grants()
        return {
            "status": "approved",
            "grant_id": grant_id,
            "device_id": device_id,
            "code": code,
            "expires_at": grant["expires_at"],
            "duration": grant["duration"],
        }

    def find_mobile_grant_by_device(self, device_id: str, device_secret: str) -> dict[str, Any] | None:
        device_id = str(device_id or "").strip()
        device_secret = str(device_secret or "").strip()
        if not device_id or not device_secret:
            return None
        device_secret_hash = sha256_hex(device_secret)
        now = time.time()
        stale_grants: list[str] = []
        for grant_id, grant in self.mobile_grants.items():
            if not isinstance(grant, dict):
                stale_grants.append(grant_id)
                continue
            expires_at = float(grant.get("expires_at") or 0)
            if expires_at and expires_at < now:
                stale_grants.append(grant_id)
                continue
            if device_id != str(grant.get("device_id") or ""):
                continue
            stored_secret_hash = str(grant.get("device_secret_hash") or "")
            if stored_secret_hash and secrets.compare_digest(device_secret_hash, stored_secret_hash):
                return grant
        if stale_grants:
            for grant_id in stale_grants:
                self.mobile_grants.pop(grant_id, None)
            self.save_mobile_grants()
        return None

    def renew_mobile_grant(self, grant: dict[str, Any]) -> None:
        ttl = self.mobile_grant_ttl_seconds(str(grant.get("duration") or MOBILE_DEFAULT_GRANT_DURATION))
        now = time.time()
        grant["last_used_at"] = now
        if ttl:
            expires_at = float(grant.get("expires_at") or 0)
            if expires_at - now < ttl / 2:
                grant["expires_at"] = now + ttl
        self.save_mobile_grants()

    def require_mobile_grant(self, body: dict[str, Any]) -> dict[str, Any]:
        grant_id = str(body.get("grant_id") or "")
        device_id = str(body.get("device_id") or "").strip()
        device_secret = str(body.get("device_secret") or "").strip()
        grant = self.mobile_grants.get(grant_id)
        if not grant:
            grant = self.find_mobile_grant_by_device(device_id, device_secret)
        expires_at = float(grant.get("expires_at") or 0) if grant else 0
        if not grant or (expires_at and expires_at < time.time()):
            self.mobile_grants.pop(grant_id, None)
            self.save_mobile_grants()
            raise ValueError("mobile console is not approved")
        stored_secret_hash = str(grant.get("device_secret_hash") or "")
        if not stored_secret_hash:
            self.mobile_grants.pop(grant_id, None)
            self.save_mobile_grants()
            raise ValueError("mobile console approval must be renewed")
        if device_id != str(grant.get("device_id") or "") or not secrets.compare_digest(sha256_hex(device_secret), stored_secret_hash):
            raise ValueError("mobile device identity did not match approval")
        self.renew_mobile_grant(grant)
        return grant

    def active_mobile_sessions(self) -> list[dict[str, Any]]:
        with self.sessions_lock:
            session_ids = list(self.sessions.keys())
        return [self.session_snapshot(session_id, 0) for session_id in session_ids]

    def workspace_is_allowed(self, workspace: Path) -> bool:
        resolved = workspace.expanduser().resolve()
        return any(resolved == allowed or allowed in resolved.parents for allowed in self.allowed_workspaces)

    def proc_info(self, pid: int) -> dict[str, Any] | None:
        entry = Path("/proc") / str(pid)
        try:
            argv = [part.decode("utf-8", "replace") for part in (entry / "cmdline").read_bytes().split(b"\x00") if part]
            cmdline = " ".join(argv).strip()
            cwd = (entry / "cwd").resolve()
        except OSError:
            return None
        return {
            "pid": pid,
            "argv": argv,
            "command": cmdline,
            "workspace": str(cwd),
            "workspace_allowed": self.workspace_is_allowed(cwd),
            "codex_session_id": self.codex_session_id_from_argv(argv),
        }

    @staticmethod
    def codex_session_id_from_argv(argv: list[str]) -> str:
        for index, arg in enumerate(argv):
            if arg == "resume" and index + 1 < len(argv):
                candidate = str(argv[index + 1] or "")
                if candidate and not candidate.startswith("-"):
                    return candidate
        return ""

    @staticmethod
    def is_codex_process(info: dict[str, Any]) -> bool:
        argv = [str(item) for item in info.get("argv") or []]
        command = str(info.get("command") or "").lower()
        if "run_agentkernel_lite_codex_bridge.py" in command or "run_agentkernel_lite_computer_bridge.py" in command:
            return False
        for arg in argv:
            name = Path(arg).name.lower()
            value = arg.lower()
            if name == "codex" or name.startswith("codex-") or "/codex" in value or "openai-codex" in value:
                return True
        return False

    def proc_children_map(self) -> dict[int, list[int]]:
        children: dict[int, list[int]] = {}
        proc = Path("/proc")
        if not proc.exists():
            return children
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat_text = (entry / "stat").read_text()
                after_name = stat_text.rsplit(")", 1)[1].strip().split()
                ppid = int(after_name[1])
                pid = int(entry.name)
            except Exception:
                continue
            children.setdefault(ppid, []).append(pid)
        return children

    def descendant_pids(self, root_pid: int, children: dict[int, list[int]]) -> list[int]:
        out: list[int] = []
        stack = list(children.get(root_pid, []))
        seen: set[int] = set()
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            out.append(pid)
            stack.extend(children.get(pid, []))
        return out

    def discover_external_codex_sessions(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        proc = Path("/proc")
        if not proc.exists():
            return rows
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            info = self.proc_info(int(entry.name))
            if not info or not self.is_codex_process(info):
                continue
            rows.append({
                "pid": info["pid"],
                "provider": "codex",
                "workspace": info["workspace"],
                "workspace_allowed": info["workspace_allowed"],
                "command": str(info["command"])[:1000],
                "codex_session_id": info.get("codex_session_id") or "",
            })
        return rows[:50]

    def recent_codex_chats(self, limit: int = 30) -> list[dict[str, Any]]:
        history_path = Path.home() / ".codex" / "history.jsonl"
        if not history_path.exists():
            return []
        try:
            lines = history_path.read_text(encoding="utf-8", errors="replace").splitlines()[-5000:]
        except Exception:
            return []
        grouped: dict[str, dict[str, Any]] = {}
        for line in lines:
            try:
                item = json.loads(line)
            except Exception:
                continue
            session_id = str(item.get("session_id") or "").strip()
            text = str(item.get("text") or "").strip()
            if not session_id or not text:
                continue
            ts = float(item.get("ts") or 0)
            row = grouped.setdefault(session_id, {
                "session_id": session_id,
                "codex_session_id": session_id,
                "provider": "codex",
                "message_count": 0,
                "started_at": ts,
                "updated_at": ts,
                "preview": "",
                "messages": [],
            })
            row["message_count"] = int(row.get("message_count") or 0) + 1
            row["updated_at"] = max(float(row.get("updated_at") or 0), ts)
            row["started_at"] = min(float(row.get("started_at") or ts), ts)
            messages = row.get("messages") if isinstance(row.get("messages"), list) else []
            messages.append({"ts": ts, "text": text[-500:]})
            row["messages"] = messages[-5:]
            row["preview"] = text[-500:]
        metadata = self.codex_thread_metadata(set(grouped.keys()))
        for session_id, row in grouped.items():
            meta = metadata.get(session_id) or {}
            workspace = str(meta.get("cwd") or "")
            row["workspace"] = workspace
            row["workspace_allowed"] = self.workspace_is_allowed(Path(workspace)) if workspace else False
            row["title"] = str(meta.get("title") or "")
            row["updated_at"] = max(float(row.get("updated_at") or 0), float(meta.get("updated_at") or 0))
        return sorted(grouped.values(), key=lambda item: float(item.get("updated_at") or 0), reverse=True)[:limit]

    def codex_thread_metadata(self, session_ids: set[str]) -> dict[str, dict[str, Any]]:
        if not session_ids:
            return {}
        db_path = Path.home() / ".codex" / "state_5.sqlite"
        if not db_path.exists():
            return {}
        out: dict[str, dict[str, Any]] = {}
        try:
            connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
            placeholders = ",".join("?" for _ in session_ids)
            rows = connection.execute(
                f"select id, cwd, title, updated_at from threads where id in ({placeholders})",
                tuple(session_ids),
            ).fetchall()
            connection.close()
        except Exception:
            return out
        for session_id, cwd, title, updated_at in rows:
            out[str(session_id)] = {
                "cwd": str(cwd or ""),
                "title": str(title or ""),
                "updated_at": float(updated_at or 0),
            }
        return out

    def list_tmux_panes(self) -> list[dict[str, Any]]:
        tmux = str(self.provider_bins.get("tmux") or "tmux")
        if not (shutil.which(tmux) or Path(tmux).exists()):
            return []
        command = [
            tmux,
            "list-panes",
            "-a",
            "-F",
            "#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_id}\t#{pane_pid}\t#{pane_current_path}\t#{pane_current_command}\t#{pane_active}\t#{window_name}",
        ]
        try:
            completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, check=False)
        except (OSError, subprocess.SubprocessError):
            return []
        panes: list[dict[str, Any]] = []
        for line in completed.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 9:
                continue
            session_name, window_index, pane_index, pane_id, pane_pid, path, current_command, active, window_name = parts[:9]
            workspace = Path(path or ".").expanduser()
            try:
                workspace = workspace.resolve()
            except OSError:
                pass
            codex_processes: list[dict[str, Any]] = []
            try:
                children = self.proc_children_map()
                for pid in self.descendant_pids(int(pane_pid), children):
                    info = self.proc_info(pid)
                    if info and self.is_codex_process(info):
                        codex_processes.append({
                            "pid": info["pid"],
                            "command": str(info["command"])[:1000],
                            "workspace": info["workspace"],
                            "workspace_allowed": info["workspace_allowed"],
                            "codex_session_id": info.get("codex_session_id") or "",
                        })
            except Exception:
                codex_processes = []
            panes.append({
                "id": pane_id,
                "provider": "tmux",
                "session": session_name,
                "window": window_index,
                "pane": pane_index,
                "pane_pid": pane_pid,
                "target": pane_id,
                "workspace": str(workspace),
                "workspace_allowed": self.workspace_is_allowed(workspace),
                "command": current_command,
                "codex_detected": bool(codex_processes),
                "codex_processes": codex_processes,
                "active": active == "1",
                "title": f"{session_name}:{window_index}.{pane_index} {window_name}".strip(),
            })
        return panes[:100]

    def restore_session_index(self) -> None:
        if not self.session_index_path.exists():
            return
        try:
            payload = json.loads(self.session_index_path.read_text())
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        raw_sessions = payload.get("sessions")
        if not isinstance(raw_sessions, list):
            return
        restored: dict[str, dict[str, Any]] = {}
        for item in raw_sessions[-100:]:
            if not isinstance(item, dict):
                continue
            session_id = str(item.get("session_id") or "")
            workspace_raw = str(item.get("workspace") or "")
            codex_session_id = str(item.get("codex_session_id") or "")
            if not session_id or not workspace_raw:
                continue
            try:
                workspace = self.workspace_allowed(workspace_raw)
            except Exception:
                continue
            events = item.get("events") if isinstance(item.get("events"), list) else []
            if not codex_session_id:
                for event in events:
                    if isinstance(event, dict) and isinstance(event.get("parsed"), dict):
                        codex_session_id = self.extract_codex_thread_id(event["parsed"])
                        if codex_session_id:
                            break
            provider = str(item.get("provider") or "codex")
            if provider not in {"codex", "cursor", "tmux"}:
                provider = "codex"
            status = str(item.get("status") or "completed")
            if status == "running" and provider != "tmux":
                status = "completed"
            restored[session_id] = {
                "session_id": session_id,
                "action_id": str(item.get("action_id") or ""),
                "provider": provider,
                "name": str(item.get("name") or ""),
                "model": str(item.get("model") or ""),
                "workspace": workspace,
                "status": status,
                "started_at": float(item.get("started_at") or time.time()),
                "completed_at": item.get("completed_at"),
                "exit_code": item.get("exit_code"),
                "process": None,
                "events": events,
                "summary": str(item.get("summary") or ""),
                "error": str(item.get("error") or ""),
                "codex_session_id": codex_session_id,
                "command": self.restored_command_for_provider(provider),
                "restored": True,
                "external": bool(item.get("external")),
                "tmux": item.get("tmux") if isinstance(item.get("tmux"), dict) else None,
            }
        with self.sessions_lock:
            self.sessions.update(restored)

    @staticmethod
    def restored_command_for_provider(provider: str) -> list[str]:
        if provider == "tmux":
            return ["tmux", "attach", "..."]
        if provider == "cursor":
            return ["cursor-agent", "-p", "--output-format", "stream-json", "..."]
        return ["codex", "exec", "..."]

    def persist_session_index(self) -> None:
        with self.sessions_lock:
            sessions = [self.serializable_session(session) for session in self.sessions.values()]
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.session_index_path.write_text(json.dumps({
            "version": 1,
            "updated_at": time.time(),
            "sessions": sessions[-100:],
        }, indent=2, sort_keys=True))
        try:
            self.session_index_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    @staticmethod
    def serializable_session(session: dict[str, Any]) -> dict[str, Any]:
        events = []
        for event in list(session.get("events") or [])[-80:]:
            if not isinstance(event, dict):
                continue
            events.append({
                "index": event.get("index"),
                "stream": event.get("stream"),
                "time": event.get("time"),
                "text": str(event.get("text") or "")[-8000:],
                "parsed": event.get("parsed") if isinstance(event.get("parsed"), dict) else None,
            })
        return {
            "session_id": str(session.get("session_id") or ""),
            "action_id": str(session.get("action_id") or ""),
            "provider": str(session.get("provider") or "codex"),
            "name": str(session.get("name") or ""),
            "model": str(session.get("model") or ""),
            "workspace": str(session.get("workspace") or ""),
            "status": "completed" if session.get("status") == "running" else str(session.get("status") or "completed"),
            "started_at": float(session.get("started_at") or time.time()),
            "completed_at": session.get("completed_at"),
            "exit_code": session.get("exit_code"),
            "event_count": len(session.get("events") or []),
            "events": events,
            "summary": str(session.get("summary") or "")[-4000:],
            "error": str(session.get("error") or "")[-4000:],
            "codex_session_id": str(session.get("codex_session_id") or ""),
            "provider_session_id": str(session.get("provider_session_id") or session.get("codex_session_id") or ""),
            "cursor_session_id": str(session.get("cursor_session_id") or (session.get("codex_session_id") if session.get("provider") == "cursor" else "") or ""),
            "external": bool(session.get("external")),
            "tmux": session.get("tmux") if isinstance(session.get("tmux"), dict) else None,
        }

    def derive_key(self, grant: dict[str, Any]) -> bytes:
        browser_public = public_key_from_jwk(grant["browser_public_jwk"])
        shared = self.private_key.exchange(ec.ECDH(), browser_public)
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=str(grant["grant_id"]).encode("utf-8"),
            info=str(grant["origin"]).encode("utf-8"),
        ).derive(shared)

    def decrypt_message(self, envelope: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        grant_id = str(envelope.get("grant_id") or "")
        grant = self.grants.get(grant_id)
        if not grant:
            raise ValueError("unknown pairing grant")
        if float(grant.get("expires_at") or 0) < time.time():
            raise ValueError("pairing grant expired")
        seq = int(envelope.get("seq") or 0)
        if seq <= int(grant.get("last_seq") or 0):
            raise ValueError("stale or duplicate message sequence")
        aad = f"{grant_id}:{seq}".encode("utf-8")
        key = self.derive_key(grant)
        plaintext = AESGCM(key).decrypt(unb64(envelope["nonce"]), unb64(envelope["ciphertext"]), aad)
        now = time.time()
        grant["last_seq"] = seq
        grant["last_used_at"] = now
        grant["expires_at"] = max(float(grant.get("expires_at") or 0), now + PAIRING_GRANT_TTL_SECONDS)
        self.save_grants()
        return json.loads(plaintext.decode("utf-8")), grant

    def encrypt_response(self, grant: dict[str, Any], seq: int, payload: dict[str, Any]) -> dict[str, Any]:
        nonce = secrets.token_bytes(12)
        aad = f"{grant['grant_id']}:{seq}".encode("utf-8")
        ciphertext = AESGCM(self.derive_key(grant)).encrypt(
            nonce,
            json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            aad,
        )
        return {
            "protocol": PROTOCOL,
            "grant_id": grant["grant_id"],
            "seq": seq,
            "nonce": b64(nonce),
            "ciphertext": b64(ciphertext),
        }

    def workspace_allowed(self, raw_workspace: str) -> Path:
        workspace = Path(raw_workspace or "").expanduser().resolve()
        for allowed in self.allowed_workspaces:
            if workspace == allowed or allowed in workspace.parents:
                return workspace
        allowed_text = ", ".join(str(item) for item in self.allowed_workspaces)
        raise ValueError(f"workspace is not allowed by this bridge: {workspace} (allowed: {allowed_text})")

    def ensure_workspace_git_repo(self, workspace: Path) -> bool:
        existing = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        if existing.returncode == 0:
            return False
        initialized = subprocess.run(
            ["git", "-C", str(workspace), "init", "-b", "main"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
        if initialized.returncode != 0:
            detail = (initialized.stderr or initialized.stdout or "unknown error").strip()
            raise ValueError(f"could not initialize Git repository in {workspace}: {detail}")
        return True

    def codex_base_command(self, workspace: Path, model: str = "") -> list[str]:
        # `codex exec` is the non-interactive CLI surface. The bridge owns pairing,
        # allowed workspace roots, and provider launch policy; the browser cannot
        # override sandbox or approval behavior per request.
        self.ensure_workspace_git_repo(workspace)
        command = [
            self.provider_bin("codex"),
            "exec",
            "--json",
            "--cd",
            str(workspace),
            "--sandbox",
            self.sandbox,
        ]
        if model:
            command.extend(["--model", model])
        return command

    def cursor_base_command(self, workspace: Path, model: str = "") -> list[str]:
        # Cursor's documented headless mode is print mode with stream-json output.
        # --force keeps the bridge non-interactive; workspace and device trust are
        # enforced by this bridge before the provider process is launched.
        self.ensure_workspace_git_repo(workspace)
        command = [
            self.provider_bin("cursor"),
            "-p",
            "--output-format",
            "stream-json",
            "--force",
        ]
        if model:
            command.extend(["--model", model])
        return command

    def append_session_event(self, session_id: str, event: dict[str, Any]) -> None:
        should_persist = False
        with self.sessions_lock:
            session = self.sessions.get(session_id)
            if not session:
                return
            if self.is_duplicate_user_echo(session, event):
                return
            session["events"].append(event)
            session["events"] = session["events"][-500:]
            should_persist = True
            parsed = event.get("parsed")
            if isinstance(parsed, dict):
                thread_id = self.extract_codex_thread_id(parsed)
                if thread_id and not session.get("codex_session_id"):
                    session["codex_session_id"] = thread_id
                if thread_id:
                    session["provider_session_id"] = thread_id
                    if session.get("provider") == "cursor":
                        session["cursor_session_id"] = thread_id
                text = self.extract_codex_event_text(parsed)
                if text:
                    session["summary"] = text[-4000:]
        if should_persist:
            self.persist_session_index()

    def is_duplicate_user_echo(self, session: dict[str, Any], event: dict[str, Any]) -> bool:
        if session.get("provider") != "cursor":
            return False
        parsed = event.get("parsed")
        if not isinstance(parsed, dict) or str(parsed.get("type") or "") != "user":
            return False
        text = self.text_from_cursor_message(parsed.get("message"))
        if not text:
            return False
        for previous in reversed(list(session.get("events") or [])[-5:]):
            if previous.get("stream") != "user":
                continue
            previous_text = str(previous.get("text") or "").strip()
            if secrets.compare_digest(previous_text, text.strip()):
                return True
            return False
        return False

    def session_snapshot(self, session_id: str, since: int = 0) -> dict[str, Any]:
        self.refresh_tmux_session(session_id)
        with self.sessions_lock:
            session = self.sessions.get(session_id)
            if not session:
                raise ValueError("unknown Codex bridge session")
            events = session["events"][max(0, int(since)) :]
            return {
                "session_id": session_id,
                "codex_session_id": session.get("codex_session_id") or "",
                "provider_session_id": session.get("provider_session_id") or session.get("codex_session_id") or "",
                "cursor_session_id": session.get("cursor_session_id") or (session.get("codex_session_id") if session.get("provider") == "cursor" else ""),
                "provider": session.get("provider") or "codex",
                "name": session.get("name") or "",
                "status": session["status"],
                "workspace": str(session["workspace"]),
                "model": session.get("model") or "",
                "started_at": session["started_at"],
                "completed_at": session.get("completed_at"),
                "exit_code": session.get("exit_code"),
                "elapsed_ms": round((time.time() - float(session["started_at"])) * 1000),
                "event_count": len(session["events"]),
                "events": events,
                "summary": session.get("summary") or "",
                "error": session.get("error") or "",
                "external": bool(session.get("external")),
                "tmux": session.get("tmux") if isinstance(session.get("tmux"), dict) else None,
            }

    def capture_tmux_pane(self, target: str) -> str:
        tmux = str(self.provider_bins.get("tmux") or "tmux")
        completed = subprocess.run(
            [tmux, "capture-pane", "-p", "-J", "-S", "-200", "-t", target],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError((completed.stderr or "tmux capture failed").strip())
        return completed.stdout.rstrip()

    def refresh_tmux_session(self, session_id: str) -> None:
        with self.sessions_lock:
            session = self.sessions.get(session_id)
            if not session or session.get("provider") != "tmux":
                return
            tmux_info = session.get("tmux") if isinstance(session.get("tmux"), dict) else {}
            target = str(tmux_info.get("target") or "")
            previous = str(session.get("summary") or "")
        if not target:
            return
        try:
            text = self.capture_tmux_pane(target)
        except Exception as error:
            with self.sessions_lock:
                session = self.sessions.get(session_id)
                if session:
                    session["status"] = "failed"
                    session["error"] = str(error)
            return
        if text == previous:
            return
        event = {
            "index": 0,
            "stream": "tmux",
            "time": time.time(),
            "text": text[-8000:],
            "parsed": None,
        }
        with self.sessions_lock:
            session = self.sessions.get(session_id)
            if not session:
                return
            event["index"] = len(session.get("events") or [])
            session["events"].append(event)
            session["events"] = session["events"][-500:]
            session["summary"] = text[-4000:]
        self.persist_session_index()

    def read_stream(self, session_id: str, stream_name: str, pipe: Any) -> None:
        try:
            for line in pipe:
                text = str(line).rstrip("\n")
                if not text:
                    continue
                parsed = None
                if stream_name == "stdout":
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        parsed = None
                self.append_session_event(session_id, {
                    "index": len(self.sessions.get(session_id, {}).get("events", [])),
                    "stream": stream_name,
                    "time": time.time(),
                    "text": text[-8000:],
                    "parsed": parsed,
                })
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    def wait_for_process(self, session_id: str, process: subprocess.Popen[str]) -> None:
        exit_code = process.wait()
        with self.sessions_lock:
            session = self.sessions.get(session_id)
            if not session:
                return
            session["exit_code"] = exit_code
            session["completed_at"] = time.time()
            provider_label = "Cursor" if session.get("provider") == "cursor" else "Codex"
            if session["status"] == "cancelled":
                session["summary"] = f"{provider_label} session cancelled."
            elif exit_code == 0:
                session["status"] = "completed"
                session["summary"] = self.summarize_events(session)
            else:
                session["status"] = "failed"
                session["error"] = self.summarize_events(session, prefer_stderr=True)
        self.persist_session_index()

    def summarize_events(self, session: dict[str, Any], prefer_stderr: bool = False) -> str:
        events = session.get("events", [])
        candidates = [event for event in events if (event.get("stream") == "stderr") == prefer_stderr]
        if not candidates:
            candidates = events
        for event in reversed(candidates):
            parsed = event.get("parsed")
            if isinstance(parsed, dict):
                text = self.extract_codex_event_text(parsed)
                if text:
                    return text[-4000:]
            text = str(event.get("text") or "").strip()
            if text and not text.startswith("{"):
                return text[-4000:]
        return ""

    @staticmethod
    def extract_codex_thread_id(parsed: dict[str, Any]) -> str:
        for key in ("session_id", "conversation_id", "thread_id", "task_id"):
            value = parsed.get(key)
            if value:
                return str(value)
        if parsed.get("type") == "thread.started":
            value = parsed.get("thread_id")
            return str(value) if value else ""
        msg = parsed.get("msg")
        if isinstance(msg, dict):
            for key in ("session_id", "conversation_id", "thread_id", "task_id"):
                value = msg.get(key)
                if value:
                    return str(value)
        return ""

    @staticmethod
    def extract_codex_event_text(parsed: dict[str, Any]) -> str:
        def text_from_item(item: Any) -> str:
            if not isinstance(item, dict):
                return ""
            item_type = str(item.get("type") or "")
            if item_type == "agent_message":
                return str(item.get("text") or item.get("message") or "").strip()
            if item_type == "error":
                return str(item.get("message") or "").strip()
            return ""

        text = text_from_item(parsed.get("item"))
        if text:
            return text
        msg = parsed.get("msg")
        if isinstance(msg, dict):
            text = text_from_item(msg.get("item"))
            if text:
                return text
            if str(msg.get("type") or "") == "agent_message":
                return str(msg.get("text") or msg.get("message") or "").strip()
            if msg.get("last_agent_message"):
                return str(msg.get("last_agent_message") or "").strip()
            if str(msg.get("type") or "") in {"turn.failed", "error"}:
                error = msg.get("error")
                if isinstance(error, dict):
                    return str(error.get("message") or "").strip()
                return str(msg.get("message") or "").strip()
            if str(msg.get("type") or "") == "assistant":
                text = BridgeState.text_from_cursor_message(msg.get("message"))
                if text:
                    return text
        if parsed.get("last_agent_message"):
            return str(parsed.get("last_agent_message") or "").strip()
        if str(parsed.get("type") or "") in {"turn.failed", "error"}:
            error = parsed.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or "").strip()
            return str(parsed.get("message") or "").strip()
        if str(parsed.get("type") or "") == "assistant":
            return BridgeState.text_from_cursor_message(parsed.get("message"))
        if str(parsed.get("type") or "") == "result":
            return str(parsed.get("result") or parsed.get("message") or "").strip()
        return ""

    @staticmethod
    def text_from_cursor_message(message: Any) -> str:
        if isinstance(message, str):
            return message.strip()
        if not isinstance(message, dict):
            return ""
        chunks = []
        for part in message.get("content") or []:
            if isinstance(part, dict) and str(part.get("type") or "") == "text":
                chunks.append(str(part.get("text") or ""))
        return "".join(chunks).strip()

    def start_codex_process(
        self,
        workspace: Path,
        command: list[str],
        action_id: str = "",
        model: str = "",
        name: str = "",
        session_id: str = "",
        initial_events: list[dict[str, Any]] | None = None,
        provider: str = "codex",
    ) -> dict[str, Any]:
        provider = self.normalize_provider(provider)
        provider_label = "Cursor" if provider == "cursor" else "Codex"
        command_name = "cursor-agent" if provider == "cursor" else "codex"
        session_id = session_id or f"computer_{secrets.token_urlsafe(12)}"
        process = subprocess.Popen(
            command,
            cwd=str(workspace),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
        previous: dict[str, Any] = {}
        with self.sessions_lock:
            previous = dict(self.sessions.get(session_id) or {})
            self.sessions[session_id] = {
                "session_id": session_id,
                "action_id": action_id or str(previous.get("action_id") or ""),
                "provider": provider,
                "name": name or str(previous.get("name") or ""),
                "model": model or str(previous.get("model") or ""),
                "workspace": workspace,
                "status": "running",
                "started_at": time.time(),
                "process": process,
                "events": list(initial_events or []),
                "command": [command_name, *command[1:-1], "..."],
                "codex_session_id": str(previous.get("codex_session_id") or ""),
                "provider_session_id": str(previous.get("provider_session_id") or previous.get("codex_session_id") or ""),
                "cursor_session_id": str(previous.get("cursor_session_id") or (previous.get("codex_session_id") if provider == "cursor" else "") or ""),
                "summary": str(previous.get("summary") or ""),
                "error": "",
            }
        self.persist_session_index()
        for stream_name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            if pipe is not None:
                threading.Thread(target=self.read_stream, args=(session_id, stream_name, pipe), daemon=True).start()
        threading.Thread(target=self.wait_for_process, args=(session_id, process), daemon=True).start()
        return self.session_snapshot(session_id)

    def create_ready_codex_session(self, workspace: Path, action_id: str = "", model: str = "", name: str = "", provider: str = "codex") -> dict[str, Any]:
        provider = self.normalize_provider(provider)
        if provider not in {"codex", "cursor"}:
            raise ValueError(f"{provider} ready sessions are not implemented")
        git_initialized = self.ensure_workspace_git_repo(workspace)
        provider_label = "Cursor" if provider == "cursor" else "Codex"
        session_id = f"computer_{secrets.token_urlsafe(12)}"
        events = []
        if git_initialized:
            events.append({
                "index": 0,
                "stream": "system",
                "time": time.time(),
                "text": f"Initialized Git repository in {workspace}.",
                "parsed": None,
            })
        events.append({
            "index": len(events),
            "stream": "system",
            "time": time.time(),
            "text": f"{provider_label} terminal ready in {workspace}.",
            "parsed": None,
        })
        with self.sessions_lock:
            self.sessions[session_id] = {
                "session_id": session_id,
                "action_id": action_id,
                "provider": provider,
                "name": name,
                "model": model,
                "workspace": workspace,
                "status": "ready",
                "started_at": time.time(),
                "process": None,
                "events": events,
                "command": self.restored_command_for_provider(provider),
            }
        self.persist_session_index()
        return self.session_snapshot(session_id)

    def start_codex_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider = self.normalize_provider(str(payload.get("provider") or "codex"))
        if provider not in {"codex", "cursor"}:
            raise ValueError(f"{provider} session orchestration is not implemented yet")
        workspace = self.workspace_allowed(str(payload.get("workspace") or self.allowed_workspaces[0]))
        prompt = str(payload.get("prompt") or "").strip()
        model = str(payload.get("model") or "").strip()
        name = str(payload.get("name") or "").strip()
        if not prompt:
            return self.create_ready_codex_session(workspace, str(payload.get("action_id") or ""), model, name, provider)
        command = [*(self.cursor_base_command(workspace, model) if provider == "cursor" else self.codex_base_command(workspace, model)), prompt]
        return self.start_codex_process(
            workspace,
            command,
            str(payload.get("action_id") or ""),
            model,
            name,
            initial_events=[{
                "index": 0,
                "stream": "user",
                "time": time.time(),
                "text": prompt,
                "parsed": None,
            }],
            provider=provider,
        )

    def import_codex_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace = self.workspace_allowed(str(payload.get("workspace") or self.allowed_workspaces[0]))
        codex_session_id = str(payload.get("codex_session_id") or payload.get("thread_id") or "").strip()
        if not codex_session_id:
            raise ValueError("codex_session_id is required")
        model = str(payload.get("model") or "").strip()
        name = str(payload.get("name") or "").strip()
        session_id = f"computer_{secrets.token_urlsafe(12)}"
        with self.sessions_lock:
            self.sessions[session_id] = {
                "session_id": session_id,
                "action_id": str(payload.get("action_id") or ""),
                "provider": "codex",
                "name": name,
                "model": model,
                "workspace": workspace,
                "status": "completed",
                "started_at": time.time(),
                "completed_at": time.time(),
                "exit_code": 0,
                "process": None,
                "events": [{
                    "index": 0,
                    "stream": "system",
                    "time": time.time(),
                    "text": f"Imported Codex session {codex_session_id}. Send a follow-up to resume it.",
                    "parsed": None,
                }],
                "summary": f"Imported Codex session {codex_session_id}.",
                "error": "",
                "codex_session_id": codex_session_id,
                "command": ["codex", "exec", "resume", "..."],
                "external": True,
            }
        self.persist_session_index()
        return self.session_snapshot(session_id)

    def attach_tmux_pane(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = str(payload.get("target") or payload.get("pane_id") or "").strip()
        if not target:
            raise ValueError("tmux target is required")
        panes = self.list_tmux_panes()
        pane = next((item for item in panes if item.get("target") == target or item.get("id") == target), None)
        if not pane:
            raise ValueError("tmux pane was not found")
        workspace = self.workspace_allowed(str(payload.get("workspace") or pane.get("workspace") or self.allowed_workspaces[0]))
        name = str(payload.get("name") or pane.get("title") or "").strip()
        session_id = f"computer_{secrets.token_urlsafe(12)}"
        text = self.capture_tmux_pane(target)
        with self.sessions_lock:
            self.sessions[session_id] = {
                "session_id": session_id,
                "action_id": str(payload.get("action_id") or ""),
                "provider": "tmux",
                "name": name,
                "model": "",
                "workspace": workspace,
                "status": "running",
                "started_at": time.time(),
                "process": None,
                "events": [{
                    "index": 0,
                    "stream": "tmux",
                    "time": time.time(),
                    "text": text[-8000:] or f"Attached tmux pane {target}.",
                    "parsed": None,
                }],
                "summary": text[-4000:] or f"Attached tmux pane {target}.",
                "error": "",
                "command": ["tmux", "attach", target],
                "tmux": {
                    "target": target,
                    "session": pane.get("session") or "",
                    "window": pane.get("window") or "",
                    "pane": pane.get("pane") or "",
                    "command": pane.get("command") or "",
                    "title": pane.get("title") or target,
                },
                "external": True,
            }
        self.persist_session_index()
        return self.session_snapshot(session_id)

    def rename_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "")
        name = str(payload.get("name") or "").strip()[:120]
        if not session_id:
            raise ValueError("session_id is required")
        with self.sessions_lock:
            session = self.sessions.get(session_id)
            if not session:
                raise ValueError("unknown Computer Use session")
            session["name"] = name
        self.persist_session_index()
        return self.session_snapshot(session_id)

    def send_codex_followup(self, payload: dict[str, Any]) -> dict[str, Any]:
        parent_id = str(payload.get("session_id") or "")
        prompt = str(payload.get("prompt") or "").strip()
        if not parent_id:
            raise ValueError("session_id is required")
        if not prompt:
            raise ValueError("prompt is required")
        with self.sessions_lock:
            parent = self.sessions.get(parent_id)
            if not parent:
                raise ValueError("unknown Codex bridge session")
            provider = str(parent.get("provider") or self.normalize_provider(str(payload.get("provider") or "codex")))
            if provider in {"codex", "cursor"}:
                if parent.get("status") == "running":
                    raise ValueError("cannot send follow-up while parent session is running")
            elif provider != "tmux":
                raise ValueError(f"{provider} follow-up orchestration is not implemented yet")
            workspace = Path(parent["workspace"]) if provider in {"codex", "cursor"} else Path(".")
            model = str(payload.get("model") or parent.get("model") or "").strip() if provider in {"codex", "cursor"} else ""
            codex_session_id = str(parent.get("provider_session_id") or parent.get("codex_session_id") or "") if provider in {"codex", "cursor"} else ""
            is_ready = parent.get("status") == "ready" if provider in {"codex", "cursor"} else False
            existing_events = list(parent.get("events") or []) if provider in {"codex", "cursor"} else []
        if provider == "tmux":
            return self.send_tmux_input(parent_id, prompt)
        if provider not in {"codex", "cursor"}:
            raise ValueError(f"{provider} follow-up orchestration is not implemented yet")
        user_event = {
            "index": len(existing_events),
            "stream": "user",
            "time": time.time(),
            "text": prompt,
            "parsed": None,
        }
        if is_ready:
            command = [*(self.cursor_base_command(workspace, model) if provider == "cursor" else self.codex_base_command(workspace, model)), prompt]
            return self.start_codex_process(
                workspace,
                command,
                str(payload.get("action_id") or ""),
                model,
                "",
                session_id=parent_id,
                initial_events=[*existing_events, user_event],
                provider=provider,
            )
        if provider == "cursor":
            command = [*self.cursor_base_command(workspace, model)]
            if codex_session_id:
                command.extend(["--resume", codex_session_id])
            else:
                command.append("--resume")
            command.append(prompt)
        else:
            command = [*self.codex_base_command(workspace, model), "resume"]
            if codex_session_id:
                command.append(codex_session_id)
            else:
                command.append("--last")
            command.append(prompt)
        return self.start_codex_process(
            workspace,
            command,
            str(payload.get("action_id") or ""),
            model,
            "",
            session_id=parent_id,
            initial_events=[*existing_events, user_event],
            provider=provider,
        )

    def send_tmux_input(self, session_id: str, prompt: str) -> dict[str, Any]:
        with self.sessions_lock:
            session = self.sessions.get(session_id)
            if not session:
                raise ValueError("unknown tmux bridge session")
            tmux_info = session.get("tmux") if isinstance(session.get("tmux"), dict) else {}
            target = str(tmux_info.get("target") or "")
            pane_command = str(tmux_info.get("command") or "")
            pane_title = str(tmux_info.get("title") or "")
        if not target:
            raise ValueError("tmux target is missing")
        tmux = str(self.provider_bins.get("tmux") or "tmux")
        codex_bin_name = Path(str(self.provider_bins.get("codex") or "codex")).name
        is_codex_pane = Path(pane_command).name == codex_bin_name or "codex" in pane_title.lower()
        submit_key = "Tab" if is_codex_pane else "Enter"
        buffer_name = f"agent-kernel-{secrets.token_hex(8)}"
        completed = subprocess.run(
            [tmux, "send-keys", "-t", target, "C-u"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError((completed.stderr or "tmux clear input failed").strip())
        completed = subprocess.run(
            [tmux, "load-buffer", "-b", buffer_name, "-"],
            text=True,
            input=prompt,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError((completed.stderr or "tmux buffer load failed").strip())
        try:
            completed = subprocess.run(
                [tmux, "paste-buffer", "-b", buffer_name, "-t", target],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=3,
                check=False,
            )
            if completed.returncode != 0:
                raise ValueError((completed.stderr or "tmux paste failed").strip())
            time.sleep(0.2)
            completed = subprocess.run(
                [tmux, "send-keys", "-t", target, submit_key],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=3,
                check=False,
            )
            if completed.returncode != 0:
                raise ValueError((completed.stderr or "tmux submit failed").strip())
        finally:
            subprocess.run(
                [tmux, "delete-buffer", "-b", buffer_name],
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
        with self.sessions_lock:
            session = self.sessions.get(session_id)
            if session:
                session["events"].append({
                    "index": len(session.get("events") or []),
                    "stream": "user",
                    "time": time.time(),
                    "text": prompt,
                    "parsed": None,
                })
                session["status"] = "running"
        time.sleep(0.15)
        self.refresh_tmux_session(session_id)
        return self.session_snapshot(session_id)

    def cancel_codex_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "")
        tmux_session = False
        with self.sessions_lock:
            session = self.sessions.get(session_id)
            if not session:
                raise ValueError("unknown Codex bridge session")
            if session.get("provider") == "tmux":
                session["status"] = "running"
                tmux_session = True
            process = session.get("process")
            if not tmux_session and session.get("status") == "running" and process and process.poll() is None:
                session["status"] = "cancelled"
                process.terminate()
        self.persist_session_index()
        return self.session_snapshot(session_id)

    def close_codex_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "")
        with self.sessions_lock:
            session = self.sessions.pop(session_id, None)
            if not session:
                return {"status": "missing", "session_id": session_id}
            process = session.get("process")
            if session.get("provider") == "tmux":
                was_running = False
            else:
                was_running = session.get("status") == "running" and process and process.poll() is None
            if was_running:
                process.terminate()
        self.persist_session_index()
        return {
            "status": "cancelled" if was_running else "closed",
            "session_id": session_id,
            "codex_session_id": session.get("codex_session_id") or "",
            "provider_session_id": session.get("provider_session_id") or session.get("codex_session_id") or "",
            "cursor_session_id": session.get("cursor_session_id") or (session.get("codex_session_id") if session.get("provider") == "cursor" else ""),
            "workspace": str(session.get("workspace") or ""),
            "model": session.get("model") or "",
            "event_count": len(session.get("events") or []),
            "summary": f"{'Cursor' if session.get('provider') == 'cursor' else 'Codex'} session terminated." if was_running else session.get("summary") or "",
        }

    def read_diff(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "")
        workspace_raw = str(payload.get("workspace") or "")
        if session_id:
            with self.sessions_lock:
                session = self.sessions.get(session_id)
                if not session:
                    raise ValueError("unknown Codex bridge session")
                workspace = Path(session["workspace"])
        else:
            workspace = self.workspace_allowed(workspace_raw)
        completed = subprocess.run(
            ["git", "diff", "--", "."],
            cwd=str(workspace),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        diff = completed.stdout
        truncated = len(diff) > 200_000
        if truncated:
            diff = diff[:200_000]
        return {
            "status": "ok" if completed.returncode == 0 else "failed",
            "workspace": str(workspace),
            "exit_code": completed.returncode,
            "diff": diff,
            "truncated": truncated,
            "stderr": completed.stderr[-4000:],
        }


MOBILE_PAGE_HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent Kernel Mobile Computer Use</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #101418; color: #eef4f2; }
    header, main, form { padding: 14px; }
    header { border-bottom: 1px solid #26323a; position: sticky; top: 0; background: #101418; z-index: 2; }
    h1 { font-size: 18px; margin: 0 0 4px; }
    h2 { font-size: 15px; margin: 18px 0 8px; }
    .muted { color: #aebbc2; font-size: 13px; }
    button, input, select, textarea { width: 100%; font: inherit; border-radius: 8px; border: 1px solid #33434c; padding: 10px; margin-top: 8px; }
    button { background: #1d4ed8; color: #ffffff; font-weight: 700; border: 0; }
    button:disabled { opacity: 0.55; }
    button.secondary { background: #24313a; color: #eef4f2; }
    textarea { min-height: 88px; resize: vertical; background: #182128; color: #eef4f2; }
    input, select { background: #182128; color: #eef4f2; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .session { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: stretch; border: 1px solid #2c3a43; border-radius: 8px; padding: 10px; margin: 10px 0; background: #151d23; }
    .session > button { margin-top: 0; }
    .session > button.danger { width: auto; min-width: 72px; }
    .sessionOpen { margin-top: 0; text-align: left; background: transparent; color: #eef4f2; border: 0; padding: 0; }
    .sectionTitle { margin: 16px 0 6px; color: #aebbc2; font-size: 12px; font-weight: 700; text-transform: uppercase; }
    .danger { background: #5f262b !important; color: #ffe5e8 !important; }
    .session strong { display: block; margin-bottom: 4px; }
    .hidden { display: none !important; }
    .messages { display: flex; flex-direction: column; gap: 8px; padding-bottom: 150px; }
    .message { border: 1px solid #2c3a43; border-radius: 10px; padding: 10px; background: #151d23; }
    .message.user { margin-left: 22px; background: #173422; border-color: #28593a; }
    .message.agent { margin-right: 22px; background: #111c24; border-color: #28485b; }
    .message.action { padding: 0; background: #11171c; border-style: dashed; }
    .message.error { background: #2a1719; border-color: #7a363c; }
    .message.system { padding: 8px 10px; background: #151a20; opacity: 0.82; }
    .meta { color: #aebbc2; font-size: 12px; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.04em; }
    .content { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.45; }
    .content p { margin: 0 0 8px; }
    .content p:last-child { margin-bottom: 0; }
    .actionDetails { width: 100%; }
    .actionDetails summary { display: flex; align-items: center; gap: 6px; min-height: 38px; padding: 9px 10px; color: #c7d6dd; cursor: pointer; list-style: none; }
    .actionDetails summary::-webkit-details-marker { display: none; }
    .actionDetails summary::before { content: ">"; color: #7f929d; font-size: 18px; line-height: 1; transition: transform 0.16s ease; }
    .actionDetails[open] summary::before { transform: rotate(90deg); }
    .actionDetails .content { border-top: 1px solid #26323a; padding: 10px; color: #d8e4e8; font-size: 13px; }
    .actionLabel { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    pre, code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #0b1014; border: 1px solid #26323a; padding: 10px; border-radius: 8px; margin: 8px 0 0; }
    .statusPill { display: inline-block; padding: 2px 7px; border-radius: 999px; background: #22313a; color: #c7d6dd; font-size: 12px; margin-left: 6px; }
    form { position: fixed; left: 0; right: 0; bottom: 0; background: #101418; border-top: 1px solid #26323a; }
  </style>
</head>
<body>
  <header>
    <h1>Computer Use</h1>
    <div id="status" class="muted">Not approved.</div>
    <label class="muted">Remember phone
      <select id="approvalDurationSelect">
        <option value="24h">24 hours</option>
        <option value="week">1 week</option>
        <option value="30d">30 days</option>
        <option value="unlimited">Unlimited</option>
      </select>
    </label>
    <button id="approveButton" type="button">Approve This Phone</button>
  </header>
  <main>
    <section id="listView">
      <button id="newSessionButton" type="button">New Terminal</button>
      <div class="row">
        <button id="scanButton" class="secondary" type="button">Scan PC</button>
        <button id="importButton" class="secondary" type="button">Import Codex</button>
      </div>
      <h2>Active Sessions</h2>
      <div id="sessionList"></div>
    </section>
    <section id="newView" class="hidden">
      <button id="cancelNewButton" class="secondary" type="button">Back</button>
      <h2>New Terminal</h2>
      <label>Provider
        <select id="providerSelect"><option value="codex">Codex</option></select>
      </label>
      <label>Session name <input id="sessionNameInput" type="text" placeholder="Frontend fix, prod shell, notes..." autocomplete="off"></label>
      <label>Workspace root <select id="workspaceRootSelect"></select></label>
      <label>Repo or workspace path <input id="workspaceInput" type="text" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
      <button id="startTerminalButton" type="button">Start Terminal</button>
    </section>
    <section id="chatView" class="hidden">
      <div class="row">
        <button id="backButton" class="secondary" type="button">Back</button>
        <button id="renameSessionButton" class="secondary" type="button">Rename</button>
        <button id="closeSessionButton" class="danger" type="button">Close</button>
      </div>
      <div class="row">
        <button id="expandDetailsButton" class="secondary" type="button">Expand Details</button>
        <button id="collapseDetailsButton" class="secondary" type="button">Collapse Details</button>
      </div>
      <h2 id="chatTitle">Session</h2>
      <div class="messages" id="messages"></div>
    </section>
  </main>
  <form id="messageForm" class="hidden">
    <textarea id="promptInput" placeholder="Message Codex..."></textarea>
    <button id="sendButton" type="submit">Send</button>
  </form>
  <script>
    const TOKEN = __TOKEN__;
    const WORKSPACES = __WORKSPACES__;
    const MOBILE_GRANT_KEY = 'agent-kernel-mobile-grant';
    const MOBILE_DEVICE_ID_KEY = 'agent-kernel-mobile-device-id';
    const MOBILE_DEVICE_SECRET_KEY = 'agent-kernel-mobile-device-secret';
    let grantId = localStorage.getItem('agent-kernel-mobile-grant') || '';
    let mobileDeviceId = localStorage.getItem(MOBILE_DEVICE_ID_KEY) || '';
    let mobileDeviceSecret = localStorage.getItem(MOBILE_DEVICE_SECRET_KEY) || '';
    let activeSessionId = localStorage.getItem('agent-kernel-mobile-session') || '';
    let mode = 'list';
    let externalSessions = [];
    let tmuxPanes = [];
    const statusEl = document.getElementById('status');
    const approveButton = document.getElementById('approveButton');
    const approvalDurationSelect = document.getElementById('approvalDurationSelect');
    const listView = document.getElementById('listView');
    const newView = document.getElementById('newView');
    const chatView = document.getElementById('chatView');
    const messageForm = document.getElementById('messageForm');
    const providerSelect = document.getElementById('providerSelect');
    const sessionNameInput = document.getElementById('sessionNameInput');
    const workspaceRootSelect = document.getElementById('workspaceRootSelect');
    const workspaceInput = document.getElementById('workspaceInput');
    const sessionList = document.getElementById('sessionList');
    const messages = document.getElementById('messages');
    const promptInput = document.getElementById('promptInput');
    const sendButton = document.getElementById('sendButton');
    const chatTitle = document.getElementById('chatTitle');
    const scanButton = document.getElementById('scanButton');
    const importButton = document.getElementById('importButton');
    const expandDetailsButton = document.getElementById('expandDetailsButton');
    const collapseDetailsButton = document.getElementById('collapseDetailsButton');
    function sessionDisplayName(session) {
      return String(session?.name || session?.tmux?.title || session?.workspace || 'Workspace').trim();
    }
    function randomToken(prefix = '') {
      const bytes = new Uint8Array(32);
      crypto.getRandomValues(bytes);
      let raw = '';
      for (const byte of bytes) raw += String.fromCharCode(byte);
      return `${prefix}${btoa(raw).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')}`;
    }
    function ensureMobileDeviceIdentity() {
      if (!window.crypto?.getRandomValues) {
        throw new Error('Secure random generation is required to trust this phone.');
      }
      if (!mobileDeviceId) {
        mobileDeviceId = randomToken('phone_');
        localStorage.setItem(MOBILE_DEVICE_ID_KEY, mobileDeviceId);
      }
      if (!mobileDeviceSecret) {
        mobileDeviceSecret = randomToken('');
        localStorage.setItem(MOBILE_DEVICE_SECRET_KEY, mobileDeviceSecret);
      }
      return {
        device_id: mobileDeviceId,
        device_secret: mobileDeviceSecret,
        device_name: navigator.userAgent.slice(0, 120) || 'Phone',
      };
    }
    for (const workspace of WORKSPACES) {
      const option = document.createElement('option');
      option.value = workspace;
      option.textContent = workspace;
      workspaceRootSelect.appendChild(option);
    }
    workspaceInput.value = localStorage.getItem('agent-kernel-mobile-workspace') || WORKSPACES[0] || '';
    workspaceRootSelect.value = WORKSPACES.find((root) => workspaceInput.value === root || workspaceInput.value.startsWith(`${root}/`)) || WORKSPACES[0] || '';
    workspaceRootSelect.addEventListener('change', () => {
      workspaceInput.value = workspaceRootSelect.value;
      localStorage.setItem('agent-kernel-mobile-workspace', workspaceInput.value);
    });
    workspaceInput.addEventListener('change', () => {
      localStorage.setItem('agent-kernel-mobile-workspace', workspaceInput.value.trim());
    });
    approvalDurationSelect.value = localStorage.getItem('agent-kernel-mobile-approval-duration') || '30d';
    approvalDurationSelect.addEventListener('change', () => {
      localStorage.setItem('agent-kernel-mobile-approval-duration', approvalDurationSelect.value);
    });
    function setMode(next) {
      mode = next;
      listView.classList.toggle('hidden', next !== 'list');
      newView.classList.toggle('hidden', next !== 'new');
      chatView.classList.toggle('hidden', next !== 'chat');
      messageForm.classList.toggle('hidden', next !== 'chat');
      promptInput.placeholder = 'Message Codex...';
      if (next === 'new') {
        activeSessionId = '';
        localStorage.removeItem('agent-kernel-mobile-session');
        messages.innerHTML = '';
        sessionNameInput.value = '';
        promptInput.focus();
      }
    }
    function clearMobileGrant(message = 'Not approved.') {
      grantId = '';
      activeSessionId = '';
      localStorage.removeItem(MOBILE_GRANT_KEY);
      localStorage.removeItem('agent-kernel-mobile-session');
      approveButton.classList.remove('hidden');
      approvalDurationSelect.disabled = false;
      statusEl.textContent = message;
      sessionList.innerHTML = '';
      const note = document.createElement('p');
      note.className = 'muted';
      note.textContent = 'Approve this phone from the computer bridge to start using Computer Use.';
      sessionList.appendChild(note);
      setMode('list');
    }
    async function api(path, body = {}) {
      const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token: TOKEN,
          grant_id: grantId,
          device_id: mobileDeviceId,
          device_secret: mobileDeviceSecret,
          ...body,
        }),
      });
      let payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        payload = { error: `Bridge returned HTTP ${response.status}` };
      }
      if (!response.ok) throw new Error(payload.error || payload.status || response.status);
      return payload;
    }
    function itemFromEvent(event) {
      return event?.parsed?.item || event?.parsed?.msg?.item || null;
    }
    function statusText(value) {
      return String(value || '').replace(/_/g, ' ');
    }
    function firstLine(value, limit = 96) {
      const text = String(value || '').replace(/\s+/g, ' ').trim();
      return text.length > limit ? `${text.slice(0, limit - 3)}...` : text;
    }
    function compactJson(value) {
      if (value == null || value === '') return '';
      if (typeof value === 'string') return value;
      try {
        return JSON.stringify(value, null, 2);
      } catch (_error) {
        return String(value);
      }
    }
    function messageModel(event) {
      const parsed = event?.parsed || {};
      const item = itemFromEvent(event);
      const stream = event?.stream || '';
      if (stream === 'user') return { role: 'user', label: 'You', text: event.text || '' };
      if (stream === 'system') return { role: 'system', label: 'System', text: event.text || '' };
      if (stream === 'stderr') return { role: 'error', label: 'Codex error', text: event.text || '' };
      if (item) {
        const type = item.type || '';
        if (type === 'agent_message') return { role: 'agent', label: 'Codex', text: item.text || item.message || '' };
        if (type === 'reasoning') return { role: 'action', label: 'Reasoning', summary: 'Reasoning', text: item.text || '' };
        if (type === 'command_execution') {
          const status = statusText(item.status);
          const output = item.aggregated_output ? `\n\n${item.aggregated_output}` : '';
          return { role: 'action', label: 'Command', summary: firstLine(item.command || 'Command'), pill: status, text: `${item.command || 'command'}${output}` };
        }
        if (type === 'file_change') {
          const changes = (item.changes || []).map((change) => `${statusText(change.kind)} ${change.path}`).join('\n');
          return { role: 'action', label: 'File changes', summary: firstLine(changes || 'File changes'), pill: statusText(item.status), text: changes || statusText(item.status) };
        }
        if (type === 'mcp_tool_call') {
          const detail = item.error?.message || compactJson(item.result?.structured_content || item.result?.content || item.arguments);
          return { role: item.error ? 'error' : 'action', label: `${item.server || 'tool'} / ${item.tool || 'call'}`, summary: `${item.server || 'tool'} / ${item.tool || 'call'}`, pill: statusText(item.status), text: detail };
        }
        if (type === 'collab_tool_call') return { role: 'action', label: 'Agent action', summary: firstLine(item.tool || item.name || 'Agent action'), pill: statusText(item.status), text: compactJson(item) };
        if (type === 'web_search') return { role: 'action', label: 'Web search', summary: firstLine(item.query || 'Web search'), text: item.query || compactJson(item.action) };
        if (type === 'todo_list') {
          const todos = (item.items || []).map((todo) => `${todo.completed ? '[x]' : '[ ]'} ${todo.text}`).join('\n');
          return { role: 'action', label: 'Plan', summary: 'Plan updated', text: todos };
        }
        if (type === 'error') return { role: 'error', label: 'Codex error', text: item.message || compactJson(item) };
      }
      const type = parsed.type || parsed.msg?.type || '';
      if (type === 'turn.started') return { role: 'action', label: 'Codex', summary: 'Started working', pill: 'running', text: 'Started working.' };
      if (type === 'turn.completed') {
        const usage = parsed.usage || parsed.msg?.usage || {};
        const parts = [];
        if (usage.input_tokens != null) parts.push(`${usage.input_tokens} input`);
        if (usage.output_tokens != null) parts.push(`${usage.output_tokens} output`);
        if (usage.cached_input_tokens) parts.push(`${usage.cached_input_tokens} cached`);
        return { role: 'action', label: 'Turn completed', summary: 'Turn completed', text: parts.length ? parts.join(' tokens, ') + ' tokens' : 'Completed.' };
      }
      if (type === 'turn.failed' || type === 'error') {
        const error = parsed.error || parsed.msg?.error || {};
        return { role: 'error', label: 'Codex error', text: error.message || parsed.message || parsed.msg?.message || event.text || '' };
      }
      if (parsed.last_agent_message) return { role: 'agent', label: 'Codex', text: parsed.last_agent_message };
      if (parsed.msg?.last_agent_message) return { role: 'agent', label: 'Codex', text: parsed.msg.last_agent_message };
      if (event.text && !String(event.text).startsWith('{')) return { role: 'system', label: stream || 'Bridge', text: event.text };
      return null;
    }
    function appendFormattedText(parent, text) {
      const raw = String(text || '');
      const parts = raw.split(/```/);
      for (let index = 0; index < parts.length; index += 1) {
        const value = parts[index];
        if (!value) continue;
        if (index % 2 === 1) {
          const pre = document.createElement('pre');
          pre.textContent = value.replace(/^[a-zA-Z0-9_-]+\n/, '');
          parent.appendChild(pre);
          continue;
        }
        const paragraphs = value.split(/\n{2,}/);
        for (const paragraph of paragraphs) {
          if (!paragraph.trim()) continue;
          const p = document.createElement('p');
          p.textContent = paragraph.trim();
          parent.appendChild(p);
        }
      }
    }
    function renderEvent(event) {
      const model = messageModel(event);
      if (!model || !model.text) return null;
      const wrapper = document.createElement('article');
      wrapper.className = `message ${model.role}`;
      if (model.role === 'action') {
        const details = document.createElement('details');
        details.className = 'actionDetails';
        const summary = document.createElement('summary');
        const label = document.createElement('span');
        label.className = 'actionLabel';
        label.textContent = model.summary || model.label || 'Action';
        summary.appendChild(label);
        if (model.pill) {
          const pill = document.createElement('span');
          pill.className = 'statusPill';
          pill.textContent = model.pill;
          summary.appendChild(pill);
        }
        const content = document.createElement('div');
        content.className = 'content';
        appendFormattedText(content, model.text);
        details.append(summary, content);
        wrapper.appendChild(details);
        return wrapper;
      }
      const meta = document.createElement('div');
      meta.className = 'meta';
      meta.textContent = model.label;
      if (model.pill) {
        const pill = document.createElement('span');
        pill.className = 'statusPill';
        pill.textContent = model.pill;
        meta.appendChild(pill);
      }
      const content = document.createElement('div');
      content.className = 'content';
      appendFormattedText(content, model.text);
      wrapper.append(meta, content);
      return wrapper;
    }
    function renderSession(session) {
      if (!session) return;
      const wasNearBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 80;
      statusEl.textContent = `Approved. ${session.status || 'session'}`;
      const codexId = session.codex_session_id ? ` - ${session.codex_session_id.slice(0, 8)}` : '';
      const provider = session.provider || 'codex';
      chatTitle.textContent = `${sessionDisplayName(session)} - ${provider === 'tmux' ? 'tmux' : 'Codex'} - ${session.status || ''}${codexId}`;
      sendButton.disabled = session.status === 'running' && provider !== 'tmux';
      messages.innerHTML = '';
      for (const event of session.events || []) {
        const rendered = renderEvent(event);
        if (rendered) messages.appendChild(rendered);
      }
      setMode('chat');
      if (wasNearBottom) window.setTimeout(() => window.scrollTo(0, document.body.scrollHeight), 0);
    }
    function setAllActionDetails(open) {
      for (const item of messages.querySelectorAll('.actionDetails')) item.open = Boolean(open);
    }
    async function openSession(sessionId) {
      activeSessionId = sessionId;
      localStorage.setItem('agent-kernel-mobile-session', activeSessionId);
      try {
        const detail = await api('/mobile/api/status', { session_id: activeSessionId, since: 0 });
        renderSession(detail);
      } catch (error) {
        activeSessionId = '';
        localStorage.removeItem('agent-kernel-mobile-session');
        statusEl.textContent = error.message || 'Session is no longer available.';
        setMode('list');
        refresh();
      }
    }
    async function renameActiveSession() {
      if (!activeSessionId) return;
      const current = chatTitle.textContent.split(' - ')[0] || '';
      const name = prompt('Session name', current);
      if (name == null) return;
      statusEl.textContent = 'Renaming session...';
      try {
        const result = await api('/mobile/api/rename', { session_id: activeSessionId, name });
        renderSession(result);
        window.setTimeout(refresh, 300);
      } catch (error) {
        statusEl.textContent = error.message || 'Could not rename session.';
      }
    }
    async function closeSession(sessionId = activeSessionId) {
      if (!sessionId) return;
      statusEl.textContent = 'Closing session...';
      try {
        await api('/mobile/api/close', { session_id: sessionId });
        if (activeSessionId === sessionId) {
          activeSessionId = '';
          localStorage.removeItem('agent-kernel-mobile-session');
          messages.innerHTML = '';
          setMode('list');
        }
        await refresh();
      } catch (error) {
        statusEl.textContent = error.message || 'Could not close session.';
      }
    }
    function appendSectionTitle(text) {
      const title = document.createElement('div');
      title.className = 'sectionTitle';
      title.textContent = text;
      sessionList.appendChild(title);
    }
    async function importCodexSession(row = {}) {
      const codexSessionId = prompt('Codex session id to resume', row.codex_session_id || '');
      if (!codexSessionId) return;
      const workspace = prompt('Workspace for this session', row.workspace || workspaceInput.value.trim() || workspaceRootSelect.value);
      if (!workspace) return;
      const name = prompt('Session name', row.name || '') || '';
      workspaceInput.value = workspace;
      localStorage.setItem('agent-kernel-mobile-workspace', workspace);
      statusEl.textContent = 'Importing Codex session...';
      try {
        const result = await api('/mobile/api/import', { codex_session_id: codexSessionId, workspace, name });
        activeSessionId = result.session_id;
        localStorage.setItem('agent-kernel-mobile-session', activeSessionId);
        renderSession(result);
        window.setTimeout(refresh, 500);
      } catch (error) {
        statusEl.textContent = error.message || 'Could not import Codex session.';
      }
    }
    async function attachTmuxPane(row) {
      if (!row || !row.target) return;
      statusEl.textContent = 'Attaching tmux pane...';
      try {
        const name = row.name || row.title || row.target || '';
        const result = await api('/mobile/api/tmux/attach', { target: row.target, workspace: row.workspace || workspaceInput.value.trim() || workspaceRootSelect.value, name });
        activeSessionId = result.session_id;
        localStorage.setItem('agent-kernel-mobile-session', activeSessionId);
        renderSession(result);
        window.setTimeout(refresh, 500);
      } catch (error) {
        statusEl.textContent = error.message || 'Could not attach tmux pane.';
      }
    }
    async function scanComputer() {
      statusEl.textContent = 'Scanning computer...';
      try {
        const payload = await api('/mobile/api/discover');
        externalSessions = payload.external_sessions || [];
        tmuxPanes = payload.tmux_panes || [];
        statusEl.textContent = `Found ${externalSessions.length} Codex process(es), ${tmuxPanes.length} tmux pane(s).`;
        refresh();
      } catch (error) {
        statusEl.textContent = error.message || 'Could not scan computer.';
      }
    }
    function renderDiscoveryRows() {
      if (externalSessions.length) appendSectionTitle('Codex processes');
      for (const row of externalSessions) {
        const card = document.createElement('div');
        card.className = 'session';
        const openButton = document.createElement('button');
        openButton.type = 'button';
        openButton.className = 'sessionOpen';
        const title = document.createElement('strong');
        title.textContent = `Codex process ${row.pid || ''}`;
        const detail = document.createElement('span');
        detail.textContent = `${row.workspace_allowed ? 'allowed' : 'outside allowed root'} - ${row.workspace || ''}`;
        openButton.append(title, detail);
        const importRowButton = document.createElement('button');
        importRowButton.type = 'button';
        importRowButton.textContent = 'Import';
        importRowButton.addEventListener('click', () => importCodexSession(row));
        card.append(openButton, importRowButton);
        sessionList.appendChild(card);
      }
      if (tmuxPanes.length) appendSectionTitle('tmux panes');
      for (const row of tmuxPanes) {
        const card = document.createElement('div');
        card.className = 'session';
        const openButton = document.createElement('button');
        openButton.type = 'button';
        openButton.className = 'sessionOpen';
        const title = document.createElement('strong');
        title.textContent = row.title || row.target || 'tmux pane';
        const detail = document.createElement('span');
        detail.textContent = `${row.workspace_allowed ? 'allowed' : 'outside allowed root'} - ${row.workspace || ''}`;
        openButton.append(title, detail);
        const attachButton = document.createElement('button');
        attachButton.type = 'button';
        attachButton.textContent = 'Attach';
        attachButton.disabled = !row.workspace_allowed;
        attachButton.addEventListener('click', () => attachTmuxPane(row));
        card.append(openButton, attachButton);
        sessionList.appendChild(card);
      }
    }
    async function refresh() {
      approveButton.classList.toggle('hidden', Boolean(grantId));
      approvalDurationSelect.disabled = Boolean(grantId);
      if (!grantId) {
        clearMobileGrant();
        return;
      }
      let payload;
      try {
        payload = await api('/mobile/api/sessions');
      } catch (error) {
        clearMobileGrant(error.message || 'Approval expired. Approve this phone again.');
        return;
      }
      const sessions = payload.sessions || [];
      sessionList.innerHTML = '';
      if (!sessions.length && !externalSessions.length && !tmuxPanes.length) {
        const empty = document.createElement('p');
        empty.className = 'muted';
        empty.textContent = 'No sessions yet.';
        sessionList.appendChild(empty);
      }
      for (const session of sessions) {
        const card = document.createElement('div');
        card.className = 'session';
        const openButton = document.createElement('button');
        openButton.type = 'button';
        openButton.className = 'sessionOpen';
        const title = document.createElement('strong');
        title.textContent = sessionDisplayName(session);
        const detail = document.createElement('span');
        const codexId = session.codex_session_id ? ` - ${session.codex_session_id.slice(0, 8)}` : '';
        detail.textContent = `${session.status || 'session'} - ${session.model || 'default model'}${codexId}`;
        openButton.append(title, detail);
        openButton.addEventListener('click', () => openSession(session.session_id));
        const closeButton = document.createElement('button');
        closeButton.type = 'button';
        closeButton.className = 'danger';
        closeButton.textContent = 'Close';
        closeButton.addEventListener('click', () => closeSession(session.session_id));
        card.append(openButton, closeButton);
        sessionList.appendChild(card);
      }
      renderDiscoveryRows();
      statusEl.textContent = `Approved. ${sessions.length} session${sessions.length === 1 ? '' : 's'}.`;
      if (mode === 'chat' && activeSessionId) {
        try {
          const detail = await api('/mobile/api/status', { session_id: activeSessionId, since: 0 });
          renderSession(detail);
        } catch (error) {
          activeSessionId = '';
          localStorage.removeItem('agent-kernel-mobile-session');
          statusEl.textContent = error.message || 'Session is no longer available.';
          setMode('list');
        }
      }
    }
    approveButton.addEventListener('click', async () => {
      statusEl.textContent = 'Waiting for desktop approval...';
      try {
        const duration = approvalDurationSelect.value || '30d';
        localStorage.setItem('agent-kernel-mobile-approval-duration', duration);
        const device = ensureMobileDeviceIdentity();
        const payload = await api('/mobile/api/approve', { duration, ...device });
        grantId = payload.grant_id;
        localStorage.setItem(MOBILE_GRANT_KEY, grantId);
        statusEl.textContent = `Approved. Code ${payload.code}`;
        approvalDurationSelect.disabled = true;
        refresh();
      } catch (error) {
        clearMobileGrant(error.message || 'Approval failed. Try again.');
      }
    });
    document.getElementById('newSessionButton').addEventListener('click', () => setMode('new'));
    scanButton.addEventListener('click', scanComputer);
    importButton.addEventListener('click', () => importCodexSession());
    document.getElementById('cancelNewButton').addEventListener('click', () => setMode('list'));
    document.getElementById('startTerminalButton').addEventListener('click', async () => {
      const workspace = workspaceInput.value.trim() || workspaceRootSelect.value;
      const name = sessionNameInput.value.trim();
      localStorage.setItem('agent-kernel-mobile-workspace', workspace);
      statusEl.textContent = 'Starting terminal...';
      try {
        const result = await api('/mobile/api/start', { workspace, provider: providerSelect.value || 'codex', name });
        activeSessionId = result.session_id;
        localStorage.setItem('agent-kernel-mobile-session', activeSessionId);
        renderSession(result);
        window.setTimeout(refresh, 500);
      } catch (error) {
        statusEl.textContent = error.message || 'Could not start terminal.';
      }
    });
    document.getElementById('backButton').addEventListener('click', () => {
      activeSessionId = '';
      localStorage.removeItem('agent-kernel-mobile-session');
      setMode('list');
      refresh();
    });
    document.getElementById('renameSessionButton').addEventListener('click', renameActiveSession);
    expandDetailsButton.addEventListener('click', () => setAllActionDetails(true));
    collapseDetailsButton.addEventListener('click', () => setAllActionDetails(false));
    document.getElementById('closeSessionButton').addEventListener('click', () => closeSession(activeSessionId));
    document.getElementById('messageForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const prompt = promptInput.value.trim();
      if (!prompt || sendButton.disabled) return;
      promptInput.value = '';
      sendButton.disabled = true;
      let resultStatus = '';
      let resultProvider = providerSelect.value || 'codex';
      const workspace = workspaceInput.value.trim() || workspaceRootSelect.value;
      localStorage.setItem('agent-kernel-mobile-workspace', workspace);
      const body = { workspace, provider: providerSelect.value || 'codex', prompt };
      try {
        const result = activeSessionId
          ? await api('/mobile/api/send', { ...body, session_id: activeSessionId })
          : await api('/mobile/api/start', body);
        resultStatus = result.status || '';
        resultProvider = result.provider || body.provider;
        activeSessionId = result.session_id;
        localStorage.setItem('agent-kernel-mobile-session', activeSessionId);
        renderSession(result);
        window.setTimeout(refresh, 1200);
      } catch (error) {
        promptInput.value = prompt;
        statusEl.textContent = error.message || 'Message failed.';
      } finally {
        sendButton.disabled = resultStatus === 'running' && resultProvider !== 'tmux';
      }
    });
    window.setInterval(() => { if (grantId) refresh().catch(() => {}); }, 3000);
    setMode('list');
    refresh().catch((error) => { clearMobileGrant(error.message || String(error)); });
  </script>
</body>
</html>
'''


class Handler(BaseHTTPRequestHandler):
    state: BridgeState

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[bridge] {self.address_string()} {fmt % args}")

    def require_request_origin(self) -> str:
        origin = request_origin(self).rstrip("/")
        if self.state.origin_allowed_for_host(origin, self.headers.get("Host", "")):
            self.state.allowed_origins.add(origin)
            return origin
        return self.state.require_allowed_origin(origin)

    def do_OPTIONS(self) -> None:
        try:
            self.require_request_origin()
            options_response(self, 204)
        except Exception as exc:
            options_response(self, 403, {"status": "error", "error": str(exc)})

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in {"", "/", "/mobile"}:
            self.handle_mobile_page()
            return
        if path == "/pair":
            self.handle_pair_page()
            return
        if path != "/health":
            json_response(self, 404, {"status": "error", "error": "not found"})
            return
        json_response(self, 200, self.state.health_payload())

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path.startswith("/mobile/api/"):
                self.handle_mobile_api(path, read_json(self))
                return
            origin = self.require_request_origin()
            if path == "/pairing/start":
                json_response(self, 200, self.state.start_pairing_request(origin, read_json(self)))
            elif path == "/pairing/confirm":
                json_response(self, 200, self.state.confirm_pairing_request(origin, read_json(self)))
            elif path == "/v1/message":
                json_response(self, 200, self.state.encrypted_message_response(origin, read_json(self)))
            elif path == "/v1/revoke":
                self.handle_revoke(origin)
            else:
                json_response(self, 404, {"status": "error", "error": "not found"})
        except Exception as exc:
            json_response(self, 400, {"status": "error", "error": str(exc)})

    def handle_mobile_api(self, path: str, body: dict[str, Any]) -> None:
        if path == "/mobile/api/approve":
            json_response(self, 200, self.state.approve_mobile_console(
                str(body.get("token") or ""),
                str(body.get("duration") or MOBILE_DEFAULT_GRANT_DURATION),
                {
                    "device_id": body.get("device_id") or "",
                    "device_secret": body.get("device_secret") or "",
                    "device_name": body.get("device_name") or "",
                },
            ))
            return
        mobile_grant = self.state.require_mobile_grant(body)
        if path == "/mobile/api/health":
            payload = self.state.health_payload()
            payload["grant_id"] = mobile_grant.get("grant_id")
            json_response(self, 200, payload)
        elif path == "/mobile/api/sessions":
            json_response(self, 200, {
                "status": "ok",
                "grant_id": mobile_grant.get("grant_id"),
                "sessions": self.state.active_mobile_sessions(),
            })
        elif path == "/mobile/api/discover":
            json_response(self, 200, {
                "status": "ok",
                "grant_id": mobile_grant.get("grant_id"),
                "external_sessions": self.state.discover_external_codex_sessions(),
                "codex_chats": self.state.recent_codex_chats(),
                "tmux_panes": self.state.list_tmux_panes(),
            })
        elif path == "/mobile/api/import":
            json_response(self, 200, self.state.import_codex_session({
                "codex_session_id": body.get("codex_session_id") or "",
                "workspace": body.get("workspace") or "",
                "model": body.get("model") or "",
                "name": body.get("name") or "",
            }))
        elif path == "/mobile/api/tmux/attach":
            json_response(self, 200, self.state.attach_tmux_pane({
                "target": body.get("target") or "",
                "workspace": body.get("workspace") or "",
                "name": body.get("name") or "",
            }))
        elif path == "/mobile/api/start":
            json_response(self, 200, self.state.start_codex_session({
                "provider": body.get("provider") or "codex",
                "workspace": body.get("workspace") or "",
                "model": body.get("model") or "",
                "prompt": body.get("prompt") or "",
                "name": body.get("name") or "",
            }))
        elif path == "/mobile/api/rename":
            json_response(self, 200, self.state.rename_session({
                "session_id": body.get("session_id") or "",
                "name": body.get("name") or "",
            }))
        elif path == "/mobile/api/send":
            json_response(self, 200, self.state.send_codex_followup({
                "provider": body.get("provider") or "codex",
                "session_id": body.get("session_id") or "",
                "model": body.get("model") or "",
                "prompt": body.get("prompt") or "",
            }))
        elif path == "/mobile/api/cancel":
            json_response(self, 200, self.state.cancel_codex_session({
                "session_id": body.get("session_id") or "",
                "provider": body.get("provider") or "codex",
            }))
        elif path == "/mobile/api/status":
            json_response(self, 200, self.state.session_snapshot(str(body.get("session_id") or ""), int(body.get("since") or 0)))
        elif path == "/mobile/api/close":
            json_response(self, 200, self.state.close_codex_session({
                "session_id": body.get("session_id") or "",
                "provider": body.get("provider") or "codex",
            }))
        else:
            json_response(self, 404, {"status": "error", "error": "not found"})

    def handle_mobile_page(self) -> None:
        workspaces = [str(item) for item in self.state.allowed_workspaces]
        page_template = MOBILE_PAGE_HTML
        try:
            if MOBILE_PAGE_FILE.exists():
                page_template = MOBILE_PAGE_FILE.read_text(encoding="utf-8")
        except Exception:
            page_template = MOBILE_PAGE_HTML
        page = page_template.replace("__TOKEN__", json.dumps("")).replace("__WORKSPACES__", json.dumps(workspaces))
        html_response(self, 200, page)

    def handle_pair_page(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        requested_app = str((query.get("app") or [""])[0]).strip()
        app_url = requested_app or "https://peytontolbert.com/agent_kernel/"
        try:
            parsed = urlparse(app_url)
            if parsed.scheme != "https":
                raise ValueError("app URL must be https")
            query_items = parse_qs(parsed.query)
            token = secrets.token_urlsafe(18)
            query_items["computerBroker"] = ["1"]
            query_items["computerBrokerToken"] = [token]
            app_url = parsed._replace(
                query=urlencode(query_items, doseq=True),
                fragment=f"computerBroker=1&computerBrokerToken={token}",
            ).geturl()
        except Exception:
            token = secrets.token_urlsafe(18)
            app_url = f"https://peytontolbert.com/agent_kernel/?computerBroker=1&computerBrokerToken={token}#computerBroker=1&computerBrokerToken={token}"
        app_origin = f"{urlparse(app_url).scheme}://{urlparse(app_url).netloc}"
        app_url_js = json.dumps(app_url)
        app_origin_js = json.dumps(app_origin)
        page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent Kernel Computer Pairing</title>
  <style>
    body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; line-height: 1.45; background: #101418; color: #f4f7f8; }}
    main {{ max-width: 620px; margin: 0 auto; padding: 20px; }}
    button {{ font: inherit; padding: 12px 16px; border-radius: 8px; border: 0; background: #1d4ed8; color: #ffffff; font-weight: 700; }}
    code, pre {{ background: #1f2930; border-radius: 6px; padding: 2px 5px; }}
    pre {{ padding: 12px; overflow-wrap: anywhere; white-space: pre-wrap; }}
    .muted {{ color: #b8c2c8; }}
    iframe {{ width: 100%; min-height: 72vh; border: 1px solid #33414a; border-radius: 8px; background: white; }}
  </style>
</head>
<body>
  <main>
    <h1>Pair Agent Kernel</h1>
    <p class="muted">This local page lets the HTTPS Agent Kernel app talk to the desktop bridge without a hosted relay.</p>
    <p><button id="openApp" type="button">Open Agent Kernel</button></p>
    <p><button id="embedApp" type="button">Open Here</button></p>
    <p>Status: <span id="status">waiting</span></p>
    <pre id="log"></pre>
    <div id="frameHost"></div>
  </main>
  <script>
    const appUrl = {app_url_js};
    const appOrigin = {app_origin_js};
    let appWindow = null;
    const statusEl = document.getElementById('status');
    const logEl = document.getElementById('log');
    function log(line) {{
      logEl.textContent = `${{new Date().toLocaleTimeString()}} ${{line}}\\n${{logEl.textContent}}`;
    }}
    function sendReadyTo(target) {{
      if (!target) return;
      target.postMessage({{
        type: 'agent-kernel-computer-broker-ready',
        bridgeUrl: window.location.origin,
        token: new URL(appUrl).searchParams.get('computerBrokerToken') || '',
      }}, appOrigin);
    }}
    function sendReady() {{
      if (appWindow && !appWindow.closed) sendReadyTo(appWindow);
      const frame = document.getElementById('agentKernelFrame');
      if (frame && frame.contentWindow) sendReadyTo(frame.contentWindow);
    }}
    document.getElementById('openApp').addEventListener('click', () => {{
      appWindow = window.open(appUrl, 'agent-kernel-lite');
      statusEl.textContent = appWindow ? 'app opened' : 'popup blocked';
      log(appWindow ? 'Opened Agent Kernel app.' : 'Popup blocked. Allow popups for this local pairing page.');
      sendReady();
    }});
    document.getElementById('embedApp').addEventListener('click', () => {{
      let frame = document.getElementById('agentKernelFrame');
      if (!frame) {{
        frame = document.createElement('iframe');
        frame.id = 'agentKernelFrame';
        frame.allow = 'clipboard-read; clipboard-write';
        document.getElementById('frameHost').appendChild(frame);
      }}
      frame.src = appUrl;
      appWindow = frame.contentWindow;
      statusEl.textContent = 'app embedded';
      log('Embedded Agent Kernel app.');
      frame.addEventListener('load', sendReady);
    }});
    window.addEventListener('message', async (event) => {{
      if (event.origin !== appOrigin) return;
      const message = event.data || {{}};
      if (message.type === 'agent-kernel-computer-broker-hello') {{
        appWindow = event.source;
        statusEl.textContent = 'connected';
        log('Agent Kernel app connected to local broker.');
        sendReady();
        return;
      }}
      if (message.type !== 'agent-kernel-computer-broker-request') return;
      const requestId = message.requestId;
      try {{
        const response = await fetch(message.path, {{
          method: message.method || 'GET',
          headers: message.body ? {{ 'Content-Type': 'application/json' }} : undefined,
          body: message.body ? JSON.stringify(message.body) : undefined,
          cache: 'no-store',
        }});
        let payload = null;
        try {{ payload = await response.json(); }} catch (_) {{ payload = {{ status: response.statusText || 'empty' }}; }}
        event.source.postMessage({{
          type: 'agent-kernel-computer-broker-response',
          requestId,
          ok: response.ok,
          status: response.status,
          payload,
        }}, event.origin);
      }} catch (error) {{
        event.source.postMessage({{
          type: 'agent-kernel-computer-broker-response',
          requestId,
          ok: false,
          status: 0,
          error: error.message || String(error),
        }}, event.origin);
      }}
    }});
    window.setInterval(sendReady, 1000);
  </script>
</body>
</html>
"""
        raw = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def handle_pairing_start(self, origin: str) -> None:
        self.state.cleanup_pairing_requests()
        body = read_json(self)
        browser_public_jwk = body.get("browser_public_jwk")
        public_key_from_jwk(browser_public_jwk)
        pairing_id = f"pair_{secrets.token_urlsafe(12)}"
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = time.time() + 300
        self.state.pairing[pairing_id] = {
            "pairing_id": pairing_id,
            "code": code,
            "origin": origin,
            "browser_public_jwk": browser_public_jwk,
            "expires_at": expires_at,
            "attempts": 0,
        }
        print("", flush=True)
        print("Agent Kernel Lite computer-use pairing request", flush=True)
        print(f"Origin: {origin}", flush=True)
        print(f"Pairing code: {code}", flush=True)
        print(f"Browser key fingerprint: {self.state.pairing_fingerprint(self.state.pairing[pairing_id])}", flush=True)
        print("Enter this code in the Agent Kernel Lite app within 5 minutes.", flush=True)
        print("The computer must also approve the pairing after the code is entered.", flush=True)
        print("", flush=True)
        json_response(
            self,
            200,
            {
                "status": "pairing_code_required",
                "pairing_id": pairing_id,
                "protocol": PROTOCOL,
                "bridge_public_jwk": public_key_to_jwk(self.state.private_key.public_key()),
                "expires_at": expires_at,
                "code_length": 6,
            },
        )

    def handle_pairing_confirm(self, origin: str) -> None:
        body = read_json(self)
        pairing_id = str(body.get("pairing_id") or "")
        code = str(body.get("code") or "").strip()
        request = self.state.pairing.get(pairing_id)
        if not request or float(request["expires_at"]) < time.time():
            raise ValueError("pairing request expired or missing")
        if origin != request.get("origin"):
            raise ValueError("pairing origin does not match request origin")
        request["attempts"] = int(request.get("attempts") or 0) + 1
        if int(request["attempts"]) > 5:
            self.state.pairing.pop(pairing_id, None)
            raise ValueError("too many pairing attempts")
        if not secrets.compare_digest(code, str(request["code"])):
            raise ValueError("pairing code did not match")
        self.state.approve_pairing_on_computer(request)
        grant_id = f"grant_{secrets.token_urlsafe(18)}"
        grant = {
            "grant_id": grant_id,
            "origin": request["origin"],
            "browser_public_jwk": request["browser_public_jwk"],
            "created_at": time.time(),
            "expires_at": time.time() + PAIRING_GRANT_TTL_SECONDS,
            "last_seq": 0,
        }
        self.state.grants[grant_id] = grant
        self.state.pairing.pop(pairing_id, None)
        self.state.save_grants()
        json_response(self, 200, {"status": "paired", "grant_id": grant_id, "expires_at": grant["expires_at"]})

    def handle_encrypted_message(self, origin: str) -> None:
        envelope = read_json(self)
        if envelope.get("protocol") not in {PROTOCOL, LEGACY_PROTOCOL}:
            raise ValueError("unsupported protocol")
        seq = int(envelope.get("seq") or 0)
        payload, grant = self.state.decrypt_message(envelope)
        if origin != grant.get("origin"):
            raise ValueError("request origin does not match pairing grant")
        message_type = str(payload.get("type") or "")
        if message_type in {"computer.session.start", "codex.session.start"}:
            result = self.state.start_codex_session(payload)
        elif message_type in {"computer.session.send", "codex.session.send"}:
            result = self.state.send_codex_followup(payload)
        elif message_type in {"computer.session.status", "codex.session.status"}:
            session_id = str(payload.get("session_id") or "")
            result = self.state.session_snapshot(session_id, int(payload.get("since") or 0)) if session_id else {
                "status": "ok",
                "message": "bridge is ready",
                "providers": self.state.provider_catalog(),
                "active_sessions": [
                    self.state.session_snapshot(session_id, 0)
                    for session_id in list(self.state.sessions.keys())
                    if self.state.sessions.get(session_id, {}).get("status") == "running"
                ],
            }
        elif message_type in {"computer.session.cancel", "codex.session.cancel"}:
            result = self.state.cancel_codex_session(payload)
        elif message_type in {"computer.session.close", "codex.session.close"}:
            result = self.state.close_codex_session(payload)
        elif message_type in {"computer.diff.read", "codex.diff.read"}:
            result = self.state.read_diff(payload)
        elif message_type in {"computer.grant.revoke", "codex.grant.revoke"}:
            removed = self.state.grants.pop(str(grant["grant_id"]), None)
            self.state.save_grants()
            result = {"status": "revoked" if removed else "missing", "grant_id": grant["grant_id"]}
        else:
            raise ValueError(f"unsupported encrypted message type: {message_type}")
        json_response(self, 200, self.state.encrypt_response(grant, seq, {"type": f"{message_type}.result", "result": result}))

    def handle_revoke(self, _origin: str) -> None:
        raise ValueError("plaintext revoke is disabled; use encrypted codex.grant.revoke")


def run_relay_client(state: BridgeState, relay_url: str, public_base_url: str, ttl_seconds: int = 86400) -> None:
    relay_url = relay_url.rstrip("/")
    route_id = f"route_{secrets.token_urlsafe(32)}"
    pairing_code = f"{secrets.randbelow(1_000_000):06d}"
    device_id = f"desktop_{secrets.token_urlsafe(12)}"
    token = secrets.token_urlsafe(32)
    status, registered = post_json(
        f"{relay_url}/desktop/register",
        {
            "route_id": route_id,
            "pairing_code": pairing_code,
            "device_id": device_id,
            "token": token,
            "ttl_seconds": ttl_seconds,
            "label": "Agent Kernel Desktop",
        },
    )
    if status != 200 or registered.get("status") != "registered":
        raise RuntimeError(registered.get("error") or f"relay registration failed: {status}")
    bridge_url = f"{public_base_url.rstrip('/')}/bridge/{route_id}"
    print("", flush=True)
    print("Agent Kernel Lite computer-use relay connected", flush=True)
    print(f"Relay: {relay_url}", flush=True)
    print(f"Phone bridge URL: {bridge_url}", flush=True)
    print(f"Desktop pairing code: {pairing_code}", flush=True)
    print("Enter the Phone bridge URL in the app, then pair with the code shown here.", flush=True)
    print("Keep this terminal open while using the Computer Use extension.", flush=True)
    print("", flush=True)
    while True:
        status, poll = post_json(
            f"{relay_url}/desktop/poll",
            {"device_id": device_id, "token": token, "timeout_seconds": 25},
            timeout=35,
        )
        if status != 200:
            print(f"[relay] poll failed: {status} {poll.get('error') or poll}", flush=True)
            time.sleep(2)
            continue
        if poll.get("status") == "idle":
            continue
        request = poll.get("request")
        if not isinstance(request, dict):
            time.sleep(1)
            continue
        request_id = str(request.get("request_id") or "")
        response = state.handle_relay_request(request)
        post_status, posted = post_json(
            f"{relay_url}/desktop/respond",
            {
                "device_id": device_id,
                "token": token,
                "request_id": request_id,
                "status_code": int(response.get("status_code") or 200),
                "payload": response.get("payload") if isinstance(response.get("payload"), dict) else {},
            },
        )
        if post_status != 200:
            print(f"[relay] response failed: {post_status} {posted.get('error') or posted}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Agent Kernel Lite computer-use bridge.")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Address to bind. Use 127.0.0.1 for same-computer browser use, or "
            "0.0.0.0 / the computer LAN IP for phone pairing on trusted Wi-Fi."
        ),
    )
    parser.add_argument("--port", type=int, default=45731)
    parser.add_argument("--workspace", action="append", default=[], help="Allowed workspace root. Repeat for more roots.")
    parser.add_argument("--config-dir", default="~/.agent-kernel-lite/codex-bridge")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--cursor-bin", default="cursor-agent")
    parser.add_argument("--tmux-bin", default="tmux")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--sandbox",
        default="danger-full-access",
        choices=sorted(ALLOWED_SANDBOXES),
        help=(
            "Codex CLI sandbox mode. The default avoids bubblewrap failures in "
            "browser/desktop bridge environments; use workspace-write or read-only "
            "when those sandboxes work on the host."
        ),
    )
    parser.add_argument("--approval-policy", default="never", choices=sorted(ALLOWED_APPROVAL_POLICIES))
    parser.add_argument("--allow-origin", action="append", default=[], help="Allowed browser origin. Repeat for more origins.")
    parser.add_argument("--relay-url", default="", help="Internal relay API base URL, for example https://peytontolbert.com/agent_kernel/api/relay")
    parser.add_argument("--relay-public-url", default="", help="Public relay API base URL used by the phone. Defaults to --relay-url.")
    parser.add_argument("--relay-ttl", type=int, default=86400, help="Relay route lifetime in seconds.")
    parser.add_argument("--reset-trusted-devices", action="store_true", help="Forget all browser pairings and approved mobile devices, then exit.")
    parser.add_argument("--no-auto-reload", action="store_true", help="Disable automatic bridge restart when Python source files change.")
    return parser.parse_args()


def file_mtimes(paths: list[Path]) -> dict[Path, int]:
    mtimes: dict[Path, int] = {}
    for path in paths:
        try:
            mtimes[path] = path.stat().st_mtime_ns
        except OSError:
            mtimes[path] = -1
    return mtimes


def start_source_reloader(paths: list[Path], interval_seconds: float = 1.0) -> None:
    initial = file_mtimes(paths)

    def watch() -> None:
        while True:
            time.sleep(interval_seconds)
            if file_mtimes(paths) == initial:
                continue
            print("Bridge source changed; restarting computer-use bridge...", flush=True)
            os.execv(sys.executable, [sys.executable, *sys.argv])

    threading.Thread(target=watch, name="bridge-source-reloader", daemon=True).start()


def main() -> None:
    args = parse_args()
    state = BridgeState(args)
    if args.reset_trusted_devices:
        counts = state.reset_trusted_devices()
        print(
            "Reset trusted devices: "
            f"{counts['browser_pairings']} browser pairing(s), "
            f"{counts['mobile_devices']} mobile device(s).",
            flush=True,
        )
        return
    if not args.no_auto_reload:
        start_source_reloader(BRIDGE_SOURCE_FILES)
    if str(args.relay_url or "").strip():
        run_relay_client(state, str(args.relay_url).strip(), str(args.relay_public_url or args.relay_url).strip(), int(args.relay_ttl))
        return
    Handler.state = state
    server = ThreadingHTTPServer((state.host, state.port), Handler)
    print(f"Agent Kernel Lite computer-use bridge listening on http://{state.host}:{state.port}", flush=True)
    if state.host not in {"127.0.0.1", "localhost", "::1"}:
        print("Warning: bridge is reachable beyond loopback. Use trusted Wi-Fi and approve pairings only from your own devices.", flush=True)
    print(f"Allowed workspace roots: {', '.join(str(item) for item in state.allowed_workspaces)}", flush=True)
    if state.host == "0.0.0.0":
        addresses = local_ipv4_addresses()
        if addresses:
            print("Mobile Computer Use:", flush=True)
            for address in addresses:
                print(f"  http://{address}:{state.port}/", flush=True)
        else:
            print(f"Mobile Computer Use: http://<computer-lan-ip>:{state.port}/", flush=True)
    else:
        print(f"Mobile Computer Use: http://{state.host}:{state.port}/", flush=True)
    print("Keep this terminal open while using the Computer Use extension.", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
