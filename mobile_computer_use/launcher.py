"""Friendly desktop launcher for the Mobile Computer Use bridge."""

from __future__ import annotations

import argparse
import queue
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path
from tkinter import END, BooleanVar, StringVar, Tk, filedialog, messagebox, simpledialog, ttk
from typing import Any
from urllib import request as urlrequest

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
        self.workspace = StringVar(value=str(Path(args.workspace).expanduser()))
        self.port = StringVar(value=str(args.port))
        self.allow_phone = BooleanVar(value=True)
        self.status = StringVar(value="Choose a workspace, then start the bridge.")
        self.urls = StringVar(value="")
        self.providers = StringVar(value="Provider readiness appears after the bridge starts.")
        self.external_ip = StringVar(value="External IP is optional. Use it only after you intentionally set up port forwarding.")
        self.build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(150, self.process_approval_requests)
        self.root.after(2000, self.refresh_devices_loop)

    def build(self) -> None:
        self.root.title("Mobile Computer Use")
        self.root.geometry("760x720")
        self.root.minsize(620, 620)
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Mobile Computer Use", font=("", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Start the local bridge, then enter the shown URL in the iPhone or Android app.").pack(anchor="w", pady=(2, 14))

        workspace_frame = ttk.LabelFrame(frame, text="Allowed workspace")
        workspace_frame.pack(fill="x", pady=(0, 12))
        workspace_row = ttk.Frame(workspace_frame, padding=10)
        workspace_row.pack(fill="x")
        ttk.Entry(workspace_row, textvariable=self.workspace).pack(side="left", fill="x", expand=True)
        ttk.Button(workspace_row, text="Browse", command=self.choose_workspace).pack(side="left", padx=(8, 0))

        network_frame = ttk.LabelFrame(frame, text="Network")
        network_frame.pack(fill="x", pady=(0, 12))
        network_row = ttk.Frame(network_frame, padding=10)
        network_row.pack(fill="x")
        ttk.Checkbutton(network_row, text="Allow phones on this Wi-Fi to connect", variable=self.allow_phone).pack(side="left")
        ttk.Label(network_row, text="Port").pack(side="left", padx=(18, 6))
        ttk.Entry(network_row, textvariable=self.port, width=8).pack(side="left")
        external_row = ttk.Frame(network_frame, padding=(10, 0, 10, 10))
        external_row.pack(fill="x")
        ttk.Button(external_row, text="Show External IP", command=self.fetch_external_ip).pack(side="left")
        ttk.Label(external_row, textvariable=self.external_ip, wraplength=520).pack(side="left", padx=(10, 0))

        button_row = ttk.Frame(frame)
        button_row.pack(fill="x", pady=(0, 12))
        self.start_button = ttk.Button(button_row, text="Start Bridge", command=self.start_bridge)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(button_row, text="Stop", command=self.stop_bridge, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="Open Mobile Page", command=self.open_mobile_page).pack(side="left", padx=(8, 0))

        ttk.Label(frame, textvariable=self.status, wraplength=620).pack(anchor="w", pady=(0, 8))

        urls_frame = ttk.LabelFrame(frame, text="Bridge URLs")
        urls_frame.pack(fill="both", expand=True, pady=(0, 12))
        self.urls_box = ttk.Treeview(urls_frame, columns=("url",), show="headings", height=5)
        self.urls_box.heading("url", text="Use one of these in the mobile app")
        self.urls_box.pack(fill="both", expand=True, padx=10, pady=10)
        self.urls_box.bind("<Double-1>", lambda _event: self.open_selected_url())

        providers_frame = ttk.LabelFrame(frame, text="Provider readiness")
        providers_frame.pack(fill="x")
        ttk.Label(providers_frame, textvariable=self.providers, wraplength=620, padding=10).pack(anchor="w")

        devices_frame = ttk.LabelFrame(frame, text="Approved devices")
        devices_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.devices_box = ttk.Treeview(devices_frame, columns=("device", "duration", "expires"), show="headings", height=5)
        self.devices_box.heading("device", text="Device")
        self.devices_box.heading("duration", text="Duration")
        self.devices_box.heading("expires", text="Expires")
        self.devices_box.column("device", width=260)
        self.devices_box.column("duration", width=90)
        self.devices_box.column("expires", width=180)
        self.devices_box.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        ttk.Button(devices_frame, text="Remove Selected Device", command=self.remove_selected_device).pack(anchor="e", padx=10, pady=(0, 10))

    def choose_workspace(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.workspace.get() or str(Path.home()))
        if selected:
            self.workspace.set(selected)

    def make_bridge_args(self) -> argparse.Namespace:
        host = "0.0.0.0" if self.allow_phone.get() else "127.0.0.1"
        return argparse.Namespace(
            host=host,
            port=int(self.port.get() or "45731"),
            workspace=[self.workspace.get()],
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
        workspace = Path(self.workspace.get()).expanduser()
        if not workspace.exists() or not workspace.is_dir():
            messagebox.showerror("Workspace not found", "Choose an existing folder before starting the bridge.")
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
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Bridge running. Keep this window open while using Computer Use.")
        self.render_urls()
        self.render_providers()
        self.render_devices()

    def stop_bridge(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.server = None
        self.server_thread = None
        self.state = None
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status.set("Bridge stopped.")
        for item in self.urls_box.get_children():
            self.urls_box.delete(item)
        self.providers.set("Provider readiness appears after the bridge starts.")
        self.render_devices()

    def bridge_urls(self) -> list[str]:
        if not self.state:
            return []
        if self.state.host == "0.0.0.0":
            addresses = local_ipv4_addresses()
            return [f"http://{address}:{self.state.port}/" for address in addresses] or [f"http://<computer-lan-ip>:{self.state.port}/"]
        return [f"http://{self.state.host}:{self.state.port}/"]

    def render_urls(self) -> None:
        for item in self.urls_box.get_children():
            self.urls_box.delete(item)
        for url in self.bridge_urls():
            self.urls_box.insert("", END, values=(url,))

    def render_providers(self) -> None:
        if not self.state:
            return
        lines = []
        for provider in self.state.provider_catalog():
            if provider["id"] == "claude_code":
                continue
            status = "ready" if provider.get("available") else "not found"
            lines.append(f"{provider['name']}: {status} ({provider.get('binary')})")
        self.providers.set("\n".join(lines))

    def fetch_external_ip(self) -> None:
        self.external_ip.set("Checking external IP...")

        def worker() -> None:
            try:
                with urlrequest.urlopen("https://api.ipify.org", timeout=6) as response:
                    value = response.read().decode("utf-8", "replace").strip()
                port = self.port.get() or "45731"
                text = (
                    f"External URL after port forwarding: http://{value}:{port}/. "
                    "Only use this if your router forwards that port to this computer."
                )
            except Exception as exc:
                text = f"Could not determine external IP: {exc}"
            self.root.after(0, lambda: self.external_ip.set(text))

        threading.Thread(target=worker, name="external-ip-lookup", daemon=True).start()

    def render_devices(self) -> None:
        for item in self.devices_box.get_children():
            self.devices_box.delete(item)
        if not self.state:
            return
        now = time.time()
        for grant_id, grant in sorted(self.state.mobile_grants.items(), key=lambda item: str(item[1].get("device_name") or "")):
            expires_at = float(grant.get("expires_at") or 0)
            if expires_at and expires_at < now:
                continue
            expires = "Never" if not expires_at else time.strftime("%Y-%m-%d %H:%M", time.localtime(expires_at))
            self.devices_box.insert(
                "",
                END,
                iid=grant_id,
                values=(grant.get("device_name") or grant.get("device_id") or "Phone", grant.get("duration") or "30d", expires),
            )

    def refresh_devices_loop(self) -> None:
        self.render_devices()
        self.root.after(2000, self.refresh_devices_loop)

    def remove_selected_device(self) -> None:
        if not self.state:
            messagebox.showinfo("Bridge not running", "Start the bridge before managing devices.")
            return
        selected = self.devices_box.selection()
        if not selected:
            messagebox.showinfo("No device selected", "Select an approved device to remove.")
            return
        grant_id = str(selected[0])
        grant = self.state.mobile_grants.get(grant_id, {})
        name = str(grant.get("device_name") or "this device")
        if not messagebox.askyesno("Remove approved device", f"Remove {name}? The device will need to pair again."):
            return
        self.state.mobile_grants.pop(grant_id, None)
        self.state.save_mobile_grants()
        self.render_devices()

    def selected_url(self) -> str:
        selection = self.urls_box.selection()
        if selection:
            values = self.urls_box.item(selection[0], "values")
            if values:
                return str(values[0])
        urls = self.bridge_urls()
        return urls[0] if urls else ""

    def open_selected_url(self) -> None:
        url = self.selected_url()
        if url:
            webbrowser.open(url)

    def open_mobile_page(self) -> None:
        url = self.selected_url()
        if not url:
            messagebox.showinfo("Bridge not running", "Start the bridge first.")
            return
        webbrowser.open(url.rstrip("/") + "/mobile")

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
