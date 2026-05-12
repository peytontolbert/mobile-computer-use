"""Friendly desktop launcher for the Mobile Computer Use bridge."""

from __future__ import annotations

import argparse
import json
import queue
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from tkinter import END, StringVar, Tk, messagebox, simpledialog, ttk
from typing import Any

from .bridge import (
    ALLOWED_APPROVAL_POLICIES,
    ALLOWED_SANDBOXES,
    BridgeState,
    DEFAULT_ALLOWED_ORIGINS,
    Handler,
    local_ipv4_addresses,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open the Mobile Computer Use desktop launcher.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=45731)
    parser.add_argument("--workspace", default=str(Path.home()))
    parser.add_argument("--config-dir", default="~/.agent-kernel-lite/codex-bridge")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--cursor-bin", default="cursor-agent")
    parser.add_argument("--tmux-bin", default="tmux")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--sandbox", default="danger-full-access", choices=sorted(ALLOWED_SANDBOXES))
    parser.add_argument("--approval-policy", default="never", choices=sorted(ALLOWED_APPROVAL_POLICIES))
    return parser.parse_args()


class DesktopLauncher:
    def __init__(self, root: Tk, args: argparse.Namespace) -> None:
        self.root = root
        self.args = args
        self.server: ThreadingHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self.state: BridgeState | None = None
        self.approval_requests: queue.Queue[dict[str, Any]] = queue.Queue()
        self.workspace_path = str(Path(args.workspace).expanduser())
        self.mobile_grants_path = Path(args.config_dir).expanduser() / "mobile-grants.json"
        self.status = StringVar(value="Bridge is stopped.")
        self.connection_url = StringVar(value="Press Start.")
        self.build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(150, self.process_approval_requests)
        self.root.after(2000, self.refresh_devices_loop)

    def build(self) -> None:
        self.root.title("Mobile Computer Use")
        self.root.geometry("440x430")
        self.root.minsize(420, 390)
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Mobile Computer Use", font=("", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Start the bridge, then enter the URL in the mobile app.").pack(anchor="w", pady=(2, 14))

        self.toggle_button = ttk.Button(frame, text="Start", command=self.toggle_bridge)
        self.toggle_button.pack(fill="x", ipady=12, pady=(0, 12))

        ttk.Label(frame, textvariable=self.status, wraplength=390).pack(anchor="w")
        ttk.Label(frame, textvariable=self.connection_url, wraplength=390, font=("", 11, "bold")).pack(anchor="w", pady=(4, 14))

        devices_frame = ttk.LabelFrame(frame, text="Approved devices")
        devices_frame.pack(fill="both", expand=True)
        self.devices_box = ttk.Treeview(devices_frame, columns=("device", "expires"), show="headings", height=7)
        self.devices_box.heading("device", text="Device")
        self.devices_box.heading("expires", text="Expires")
        self.devices_box.column("device", width=220)
        self.devices_box.column("expires", width=130)
        self.devices_box.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        ttk.Button(devices_frame, text="Remove Device", command=self.remove_selected_device).pack(anchor="e", padx=10, pady=(0, 10))

    def toggle_bridge(self) -> None:
        if self.server:
            self.stop_bridge()
        else:
            self.start_bridge()

    def make_bridge_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            host=self.args.host,
            port=int(self.args.port),
            workspace=[self.workspace_path],
            config_dir=self.args.config_dir,
            codex_bin=self.args.codex_bin,
            claude_bin=self.args.claude_bin,
            cursor_bin=self.args.cursor_bin,
            tmux_bin=self.args.tmux_bin,
            timeout=self.args.timeout,
            sandbox=self.args.sandbox,
            approval_policy=self.args.approval_policy,
            allow_origin=list(DEFAULT_ALLOWED_ORIGINS),
            relay_url="",
            relay_public_url="",
            relay_ttl=86400,
            reset_trusted_devices=False,
            no_auto_reload=True,
            desktop_approval_handler=self.request_desktop_approval,
        )

    def start_bridge(self) -> None:
        if self.server:
            return
        workspace = Path(self.workspace_path).expanduser()
        if not workspace.exists() or not workspace.is_dir():
            messagebox.showerror("Workspace not found", f"Workspace does not exist: {workspace}")
            return
        try:
            bridge_args = self.make_bridge_args()
            self.state = BridgeState(bridge_args)
            Handler.state = self.state
            self.server = ThreadingHTTPServer((self.state.host, self.state.port), Handler)
        except Exception as exc:
            messagebox.showerror("Bridge could not start", str(exc))
            self.server = None
            self.state = None
            return
        self.server_thread = threading.Thread(target=self.server.serve_forever, name="mobile-computer-use-bridge", daemon=True)
        self.server_thread.start()
        self.toggle_button.configure(text="Stop")
        self.status.set("Bridge is running. Keep this window open.")
        self.connection_url.set(self.primary_connection_url())
        self.render_devices()

    def stop_bridge(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.server = None
        self.server_thread = None
        self.state = None
        self.toggle_button.configure(text="Start")
        self.status.set("Bridge is stopped.")
        self.connection_url.set("Press Start.")
        self.render_devices()

    def primary_connection_url(self) -> str:
        if not self.state:
            return ""
        if self.state.host == "0.0.0.0":
            addresses = local_ipv4_addresses()
            address = addresses[0] if addresses else "<computer-lan-ip>"
            return f"http://{address}:{self.state.port}/"
        return f"http://{self.state.host}:{self.state.port}/"

    def render_devices(self) -> None:
        for item in self.devices_box.get_children():
            self.devices_box.delete(item)
        mobile_grants = self.mobile_grants_for_display()
        now = time.time()
        for grant_id, grant in sorted(mobile_grants.items(), key=lambda item: str(item[1].get("device_name") or "")):
            expires_at = float(grant.get("expires_at") or 0)
            if expires_at and expires_at < now:
                continue
            expires = "Never" if not expires_at else time.strftime("%Y-%m-%d %H:%M", time.localtime(expires_at))
            self.devices_box.insert(
                "",
                END,
                iid=grant_id,
                values=(grant.get("device_name") or grant.get("device_id") or "Phone", expires),
            )

    def mobile_grants_for_display(self) -> dict[str, dict[str, Any]]:
        if self.state:
            return self.state.mobile_grants
        try:
            raw = json.loads(self.mobile_grants_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return raw if isinstance(raw, dict) else {}

    def save_mobile_grants_without_bridge(self, grants: dict[str, dict[str, Any]]) -> None:
        self.mobile_grants_path.parent.mkdir(parents=True, exist_ok=True)
        self.mobile_grants_path.write_text(json.dumps(grants, indent=2, sort_keys=True), encoding="utf-8")

    def refresh_devices_loop(self) -> None:
        self.render_devices()
        self.root.after(2000, self.refresh_devices_loop)

    def remove_selected_device(self) -> None:
        selected = self.devices_box.selection()
        if not selected:
            messagebox.showinfo("No device selected", "Select an approved device to remove.")
            return
        grant_id = str(selected[0])
        grants = self.mobile_grants_for_display()
        grant = grants.get(grant_id, {})
        name = str(grant.get("device_name") or "this device")
        if not messagebox.askyesno("Remove approved device", f"Remove {name}? The device will need to pair again."):
            return
        if self.state:
            self.state.mobile_grants.pop(grant_id, None)
            self.state.save_mobile_grants()
        else:
            grants.pop(grant_id, None)
            self.save_mobile_grants_without_bridge(grants)
        self.render_devices()

    def request_desktop_approval(self, approval_type: str, details: dict[str, Any]) -> str:
        response: queue.Queue[str] = queue.Queue(maxsize=1)
        self.approval_requests.put({"type": approval_type, "details": details, "response": response})
        return response.get()

    def process_approval_requests(self) -> None:
        try:
            request = self.approval_requests.get_nowait()
        except queue.Empty:
            self.root.after(150, self.process_approval_requests)
            return
        approval_type = str(request.get("type") or "")
        details = request.get("details") if isinstance(request.get("details"), dict) else {}
        if approval_type == "mobile":
            code = str(details.get("approval_code") or "")
            prompt = (
                f"Phone approval code: {code}\n"
                f"Device: {details.get('device_name')}\n"
                f"Fingerprint: {details.get('device_fingerprint')}\n\n"
                "Type the six-digit code shown on your phone to approve this device."
            )
            answer = simpledialog.askstring("Approve phone", prompt, parent=self.root) or ""
        else:
            prompt = (
                f"Origin: {details.get('origin')}\n"
                f"Pairing code: {details.get('code')}\n"
                f"Fingerprint: {details.get('fingerprint')}\n\n"
                "Type APPROVE to allow this browser."
            )
            answer = simpledialog.askstring("Approve browser", prompt, parent=self.root) or ""
        response = request.get("response")
        if isinstance(response, queue.Queue):
            response.put(answer.strip())
        self.root.after(600, self.render_devices)
        self.root.after(150, self.process_approval_requests)

    def close(self) -> None:
        self.stop_bridge()
        self.root.destroy()


def main() -> None:
    args = parse_args()
    root = Tk()
    DesktopLauncher(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()
