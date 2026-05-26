from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from controller import CloakController, DEFAULT_XRAY, ROOT_DIR
from models import (
    AppSettings,
    ConfigStore,
    ListenerProjectConfig,
    PingResult,
    Profile,
    count_candidates,
    import_many,
)
from profile_exporter import ProfileExportError, export_text
from system_windows import is_admin, relaunch_as_admin


class CloakWindowsApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Cloak for Windows")
        self.geometry("1040x720")
        self.minsize(920, 600)

        self.store = ConfigStore()
        self.settings = self.store.load_settings()
        self.listener_config = self.store.load_listener()
        self.profiles = self.store.load_profiles()
        self.seed_bundled_profiles_if_needed()
        self.ping_results = self.store.load_ping_results()
        self.ui_queue: queue.Queue[tuple[str, tuple]] = queue.Queue()
        self.ping_cancel = threading.Event()
        self.egress_ip: str | None = None
        self.egress_country: str | None = None

        self.controller = CloakController(
            self.store,
            self.settings,
            self.listener_config,
            emit=lambda stream, text: self.post("log", stream, text),
            state_changed=lambda: self.post("state"),
        )

        self._load_icon()
        self._configure_style()
        self._create_vars()
        self._build_shell()
        self.refresh_all()
        self.after(120, self._drain_queue)
        self.after(1000, self._tick)
        self.after(3000, self._ensure_window_visible)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def post(self, kind: str, *payload) -> None:
        self.ui_queue.put((kind, payload))

    def _load_icon(self) -> None:
        icon = ROOT_DIR / "assets" / "Cloak.png"
        if icon.exists():
            try:
                self._icon_image = tk.PhotoImage(file=str(icon))
                self.iconphoto(True, self._icon_image)
            except tk.TclError:
                self._icon_image = None

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            style.theme_use("clam")
        style.configure("Header.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Subtle.TLabel", foreground="#5f6877")
        style.configure("Status.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Metric.TLabel", font=("Segoe UI", 14, "bold"))
        style.configure("Danger.TButton", foreground="#9a1b1b")
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

    def _create_vars(self) -> None:
        self.status_var = tk.StringVar(value="Disconnected")
        self.secondary_var = tk.StringVar(value="No profile selected.")
        self.admin_var = tk.StringVar(value="Administrator" if is_admin() else "Standard user")
        self.active_profile_var = tk.StringVar()
        self.mode_var = tk.StringVar(value=self.settings.connection_mode)
        self.use_proxy_var = tk.BooleanVar(value=self.settings.use_system_proxy)
        self.lan_var = tk.BooleanVar(value=self.settings.exposes_to_lan)
        self.socks_port_var = tk.StringVar(value=str(self.settings.listen_port))
        self.http_port_var = tk.StringVar(value=str(self.settings.http_port))
        self.log_level_var = tk.StringVar(value=self.settings.log_level)
        self.logs_enabled_var = tk.BooleanVar(value=self.settings.logs_enabled)
        self.appearance_var = tk.StringVar(value=self.settings.appearance_mode)
        self.xray_path_var = tk.StringVar(value=self.settings.xray_path or str(DEFAULT_XRAY))
        self.python_path_var = tk.StringVar(value=self.settings.python_path)
        self.egress_var = tk.StringVar(value="Not resolved")
        self.uptime_var = tk.StringVar(value="0s")
        self.down_var = tk.StringVar(value="0 KB/s")
        self.up_var = tk.StringVar(value="0 KB/s")
        self.total_var = tk.StringVar(value="0 B")
        self.endpoint_var = tk.StringVar()

    def _build_shell(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Cloak for Windows", style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, textvariable=self.admin_var, style="Subtle.TLabel").pack(side=tk.RIGHT)

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill=tk.BOTH, expand=True, pady=(14, 0))

        self.dashboard_tab = ttk.Frame(self.notebook, padding=12)
        self.profiles_tab = ttk.Frame(self.notebook, padding=12)
        self.settings_tab = ttk.Frame(self.notebook, padding=12)
        self.logs_tab = ttk.Frame(self.notebook, padding=12)
        self.about_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.dashboard_tab, text="Dashboard")
        self.notebook.add(self.profiles_tab, text="Profiles")
        self.notebook.add(self.settings_tab, text="Settings")
        self.notebook.add(self.logs_tab, text="Logs")
        self.notebook.add(self.about_tab, text="About")

        self._build_dashboard()
        self._build_profiles()
        self._build_settings()
        self._build_logs()
        self._build_about()

    def _build_dashboard(self) -> None:
        top = ttk.LabelFrame(self.dashboard_tab, text="Connection", padding=16)
        top.pack(fill=tk.X)
        left = ttk.Frame(top)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(left, textvariable=self.status_var, style="Status.TLabel").pack(anchor=tk.W)
        ttk.Label(left, textvariable=self.secondary_var, style="Subtle.TLabel").pack(anchor=tk.W, pady=(4, 12))
        chooser = ttk.Frame(left)
        chooser.pack(fill=tk.X)
        ttk.Label(chooser, text="Active profile").pack(side=tk.LEFT)
        self.active_combo = ttk.Combobox(chooser, textvariable=self.active_profile_var, state="readonly", width=52)
        self.active_combo.pack(side=tk.LEFT, padx=(10, 0))
        self.active_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_active_profile_changed())
        self.connect_button = ttk.Button(top, text="Connect", style="Accent.TButton", command=self.on_connect_clicked)
        self.connect_button.pack(side=tk.RIGHT, padx=(18, 0), ipadx=20, ipady=8)

        route = ttk.LabelFrame(self.dashboard_tab, text="Routing", padding=16)
        route.pack(fill=tk.X, pady=(12, 0))
        ttk.Radiobutton(route, text="Proxy", variable=self.mode_var, value="proxy", command=self.on_settings_changed).pack(side=tk.LEFT)
        ttk.Radiobutton(route, text="Tunnel", variable=self.mode_var, value="tunnel", command=self.on_settings_changed).pack(side=tk.LEFT, padx=(18, 0))
        ttk.Checkbutton(route, text="Use Windows system proxy", variable=self.use_proxy_var, command=self.on_settings_changed).pack(side=tk.LEFT, padx=(24, 0))
        ttk.Label(route, textvariable=self.endpoint_var, style="Subtle.TLabel").pack(side=tk.RIGHT)

        metrics = ttk.LabelFrame(self.dashboard_tab, text="This session", padding=16)
        metrics.pack(fill=tk.X, pady=(12, 0))
        for title, variable in (
            ("Down", self.down_var),
            ("Up", self.up_var),
            ("Total", self.total_var),
            ("Uptime", self.uptime_var),
            ("Egress", self.egress_var),
        ):
            box = ttk.Frame(metrics)
            box.pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Label(box, text=title, style="Subtle.TLabel").pack(anchor=tk.W)
            ttk.Label(box, textvariable=variable, style="Metric.TLabel").pack(anchor=tk.W, pady=(2, 0))
        ttk.Button(metrics, text="Refresh egress", command=self.refresh_egress).pack(side=tk.RIGHT)

        hint = ttk.LabelFrame(self.dashboard_tab, text="Windows notes", padding=16)
        hint.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(
            hint,
            text=(
                "Proxy mode mirrors Cloak's macOS system proxy path. "
                "The packet listener uses WinDivert through pydivert, so the app must run as Administrator. "
                "Tunnel mode is shown for parity, but requires a WinTun/tun2socks helper before it can route all traffic."
            ),
            wraplength=900,
            style="Subtle.TLabel",
        ).pack(anchor=tk.W)
        if not is_admin():
            ttk.Button(hint, text="Relaunch as Administrator", command=self.relaunch_admin).pack(anchor=tk.W, pady=(10, 0))

    def _build_profiles(self) -> None:
        toolbar = ttk.Frame(self.profiles_tab)
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="Add", command=self.open_import_dialog).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Import file", command=self.import_file).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(toolbar, text="Rename", command=self.rename_selected_profile).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(toolbar, text="Export", command=self.export_selected_profiles).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(toolbar, text="Delete", command=self.delete_selected_profiles).pack(side=tk.LEFT, padx=(8, 0))
        self.ping_button = ttk.Button(toolbar, text="Ping all", command=self.ping_all_profiles)
        self.ping_button.pack(side=tk.LEFT, padx=(24, 0))
        self.cancel_ping_button = ttk.Button(toolbar, text="Cancel ping", command=self.cancel_ping)
        self.cancel_ping_button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(toolbar, text="Remove no ping", command=self.remove_no_ping).pack(side=tk.LEFT, padx=(8, 0))

        columns = ("active", "name", "kind", "endpoint", "sni", "ping")
        self.profile_tree = ttk.Treeview(self.profiles_tab, columns=columns, show="headings", selectmode="extended")
        self.profile_tree.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        headings = {
            "active": "Active",
            "name": "Name",
            "kind": "Kind",
            "endpoint": "Server",
            "sni": "SNI / Host",
            "ping": "Ping",
        }
        widths = {"active": 70, "name": 220, "kind": 90, "endpoint": 190, "sni": 260, "ping": 90}
        for column in columns:
            self.profile_tree.heading(column, text=headings[column])
            self.profile_tree.column(column, width=widths[column], anchor=tk.W, stretch=column in ("name", "sni"))
        self.profile_tree.bind("<Double-1>", lambda _event: self.activate_selected_profile())

    def _build_settings(self) -> None:
        self.settings_tab.columnconfigure(0, weight=1)
        self.settings_tab.columnconfigure(1, weight=1)

        cloud = ttk.LabelFrame(self.settings_tab, text="Cloudflare listener JSON", padding=12)
        cloud.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.listener_text = tk.Text(cloud, height=11, wrap=tk.NONE, font=("Consolas", 10), undo=True)
        self.listener_text.pack(fill=tk.BOTH, expand=True)
        self.listener_text.insert("1.0", self.listener_config.encode_json())
        cloud_buttons = ttk.Frame(cloud)
        cloud_buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(cloud_buttons, text="Restore default", command=self.restore_listener_default).pack(side=tk.LEFT)
        ttk.Button(cloud_buttons, text="Save", command=self.save_settings_from_controls).pack(side=tk.RIGHT)

        proxy = ttk.LabelFrame(self.settings_tab, text="Local proxy listeners", padding=12)
        proxy.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ttk.Checkbutton(proxy, text="Expose to LAN", variable=self.lan_var, command=self.on_lan_changed).grid(row=0, column=0, columnspan=2, sticky=tk.W)
        ttk.Label(proxy, text="SOCKS port").grid(row=1, column=0, sticky=tk.W, pady=(14, 0))
        ttk.Entry(proxy, textvariable=self.socks_port_var, width=10).grid(row=1, column=1, sticky=tk.W, pady=(14, 0))
        ttk.Label(proxy, text="HTTP port").grid(row=2, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Entry(proxy, textvariable=self.http_port_var, width=10).grid(row=2, column=1, sticky=tk.W, pady=(8, 0))
        ttk.Label(proxy, text="xray log level").grid(row=3, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Combobox(proxy, textvariable=self.log_level_var, values=("trace", "debug", "info", "warn", "error"), state="readonly", width=10).grid(row=3, column=1, sticky=tk.W, pady=(8, 0))
        ttk.Checkbutton(proxy, text="Capture logs", variable=self.logs_enabled_var, command=self.on_settings_changed).grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(10, 0))

        paths = ttk.LabelFrame(self.settings_tab, text="Runtime paths", padding=12)
        paths.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        paths.columnconfigure(1, weight=1)
        ttk.Label(paths, text="xray.exe").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(paths, textvariable=self.xray_path_var).grid(row=0, column=1, sticky="ew", padx=(12, 8))
        ttk.Button(paths, text="Browse", command=self.browse_xray).grid(row=0, column=2)
        ttk.Label(paths, text="Python").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Entry(paths, textvariable=self.python_path_var).grid(row=1, column=1, sticky="ew", padx=(12, 8), pady=(8, 0))
        ttk.Button(paths, text="Use current", command=lambda: self.python_path_var.set("")).grid(row=1, column=2, pady=(8, 0))

        appearance = ttk.LabelFrame(self.settings_tab, text="Appearance", padding=12)
        appearance.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Combobox(appearance, textvariable=self.appearance_var, values=("system", "light", "dark"), state="readonly", width=12).pack(side=tk.LEFT)
        ttk.Button(appearance, text="Save settings", command=self.save_settings_from_controls).pack(side=tk.RIGHT)

    def _build_logs(self) -> None:
        buttons = ttk.Frame(self.logs_tab)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="Clear", command=lambda: self.log_text.delete("1.0", tk.END)).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Open app data", command=self.show_app_data).pack(side=tk.LEFT, padx=(8, 0))
        self.log_text = tk.Text(self.logs_tab, wrap=tk.WORD, font=("Consolas", 10), state=tk.NORMAL)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

    def _build_about(self) -> None:
        text = (
            "Cloak for Windows is a Windows port of g3ntrix/Cloak.\n\n"
            "Implemented here: profile library, VLESS/Trojan/VMess/Shadowsocks import, real curl-based ping, "
            "Xray config generation, Python SNI bridge process control, Windows system proxy toggling, egress lookup, "
            "logs, and persisted settings.\n\n"
            "License: GPL-3.0, matching the upstream project."
        )
        ttk.Label(self.about_tab, text=text, wraplength=880, justify=tk.LEFT).pack(anchor=tk.NW)

    def refresh_all(self) -> None:
        self.refresh_active_combo()
        self.refresh_profile_tree()
        self.refresh_status()
        self.endpoint_var.set(
            f"SOCKS {self.settings.resolved_socks_host_for_local_client}:{self.settings.listen_port}    "
            f"HTTP {self.settings.resolved_socks_host_for_local_client}:{self.settings.http_port}"
        )

    def seed_bundled_profiles_if_needed(self) -> None:
        seeds = [
            "trojan://humanity@127.0.0.1:40443?security=tls&sni=www.ignitelimit.com&type=ws&path=/assignment&host=www.ignitelimit.com#Amirstar",
            "trojan://humanity@127.0.0.1:40443?security=tls&sni=www.creationlong.org&allowInsecure=1&type=ws&path=/assignment&host=www.creationlong.org#cloud",
        ]
        appended = False
        for raw in seeds:
            parsed, _errors = import_many(raw)
            if not parsed:
                continue
            profile = parsed[0]
            duplicate = any(
                existing.kind == profile.kind
                and existing.server == profile.server
                and existing.server_port == profile.server_port
                and existing.tls.server_name == profile.tls.server_name
                for existing in self.profiles
            )
            if not duplicate:
                self.profiles.append(profile)
                appended = True
        if appended:
            if not self.settings.active_profile_id and self.profiles:
                self.settings.active_profile_id = self.profiles[0].id
            self.store.save_profiles(self.profiles)
            self.store.save_settings(self.settings)

    def refresh_active_combo(self) -> None:
        values = [self._profile_label(profile) for profile in self.profiles]
        self.active_combo["values"] = values
        active = self.active_profile()
        self.active_profile_var.set(self._profile_label(active) if active else "")

    def refresh_profile_tree(self) -> None:
        self.profile_tree.delete(*self.profile_tree.get_children())
        for profile in self.profiles:
            result = self.ping_results.get(profile.id)
            ping = ""
            if result:
                ping = f"{result.millis} ms" if result.millis is not None else result.error or "failed"
            self.profile_tree.insert(
                "",
                tk.END,
                iid=profile.id,
                values=(
                    "Yes" if self.settings.active_profile_id == profile.id else "",
                    profile.name,
                    profile.display_kind,
                    f"{profile.server}:{profile.server_port}",
                    profile.subtitle,
                    ping,
                ),
            )

    def refresh_status(self) -> None:
        status = self.controller.status
        self.status_var.set(self.controller.status_message)
        active = self.active_profile()
        if status == "running":
            self.connect_button.configure(text="Disconnect", state=tk.NORMAL)
            self.secondary_var.set(f"Profile: {active.name if active else 'none'}")
        elif status in ("starting", "stopping"):
            self.connect_button.configure(text="Please wait", state=tk.DISABLED)
            self.secondary_var.set(self.controller.status_message)
        elif status == "error":
            self.connect_button.configure(text="Connect", state=tk.NORMAL)
            self.secondary_var.set(self.controller.status_message)
        else:
            self.connect_button.configure(text="Connect", state=tk.NORMAL)
            self.secondary_var.set("No profile selected. Import or pick one from Profiles." if not active else f"Profile: {active.name}")

    def active_profile(self) -> Profile | None:
        if not self.settings.active_profile_id:
            return self.profiles[0] if self.profiles else None
        return next((profile for profile in self.profiles if profile.id == self.settings.active_profile_id), None)

    def selected_profiles(self) -> list[Profile]:
        ids = set(self.profile_tree.selection())
        return [profile for profile in self.profiles if profile.id in ids]

    def on_active_profile_changed(self) -> None:
        label = self.active_profile_var.get()
        for profile in self.profiles:
            if self._profile_label(profile) == label:
                self.settings.active_profile_id = profile.id
                self.store.save_settings(self.settings)
                break
        self.refresh_all()

    def activate_selected_profile(self) -> None:
        selected = self.selected_profiles()
        if selected:
            self.settings.active_profile_id = selected[0].id
            self.store.save_settings(self.settings)
            self.refresh_all()

    def on_connect_clicked(self) -> None:
        if self.controller.is_running:
            self.controller.stop_async(lambda ok, msg: self.post("stop_done", ok, msg))
            return
        if not self.save_settings_from_controls(show_success=False):
            return
        active = self.active_profile()
        if not active:
            messagebox.showwarning("No profile", "Import and select a profile before connecting.")
            self.notebook.select(self.profiles_tab)
            return
        self.controller.set_settings(self.settings, self.listener_config)
        self.controller.start_async(active, lambda ok, msg: self.post("start_done", ok, msg))

    def refresh_egress(self) -> None:
        if not self.controller.is_running:
            self.egress_var.set("Connect first")
            return
        self.egress_var.set("Resolving...")
        self.controller.refresh_egress_async(lambda ok, text, country: self.post("egress", ok, text, country))

    def on_settings_changed(self) -> None:
        previous_mode = self.settings.connection_mode
        previous_proxy = self.settings.use_system_proxy
        self.settings.connection_mode = self.mode_var.get()
        self.settings.use_system_proxy = self.use_proxy_var.get()
        self.settings.logs_enabled = self.logs_enabled_var.get()
        self.store.save_settings(self.settings)
        if self.controller.is_running and (
            previous_mode != self.settings.connection_mode
            or previous_proxy != self.settings.use_system_proxy
        ):
            self.restart_connection()

    def on_lan_changed(self) -> None:
        self.settings.set_exposes_to_lan(self.lan_var.get())
        self.socks_port_var.set(str(self.settings.listen_port))
        self.save_settings_from_controls(show_success=False)

    def save_settings_from_controls(self, show_success: bool = True) -> bool:
        try:
            socks = int(self.socks_port_var.get().strip())
            http = int(self.http_port_var.get().strip())
            if not 0 < socks <= 65535 or not 0 < http <= 65535:
                raise ValueError("Ports must be between 1 and 65535.")
            listener = ListenerProjectConfig.decode(self.listener_text.get("1.0", tk.END))
        except Exception as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return False
        self.settings.listen_port = socks
        self.settings.http_port = http
        self.settings.set_exposes_to_lan(self.lan_var.get())
        self.settings.connection_mode = self.mode_var.get()
        self.settings.use_system_proxy = self.use_proxy_var.get()
        self.settings.log_level = self.log_level_var.get()
        self.settings.logs_enabled = self.logs_enabled_var.get()
        self.settings.appearance_mode = self.appearance_var.get()
        self.settings.xray_path = "" if self.xray_path_var.get().strip() == str(DEFAULT_XRAY) else self.xray_path_var.get().strip()
        self.settings.python_path = self.python_path_var.get().strip()
        self.listener_config = listener
        was_running = self.controller.is_running
        self.store.save_settings(self.settings)
        self.store.save_listener(listener)
        self.controller.set_settings(self.settings, self.listener_config)
        self.refresh_all()
        if was_running:
            self.restart_connection()
        if show_success:
            messagebox.showinfo("Saved", "Settings saved.")
        return True

    def restore_listener_default(self) -> None:
        self.listener_text.delete("1.0", tk.END)
        self.listener_text.insert("1.0", ListenerProjectConfig.default_json())

    def browse_xray(self) -> None:
        path = filedialog.askopenfilename(title="Choose xray.exe", filetypes=[("xray.exe", "xray.exe"), ("Executable", "*.exe"), ("All files", "*.*")])
        if path:
            self.xray_path_var.set(path)

    def show_app_data(self) -> None:
        messagebox.showinfo("App data", str(self.store.app_dir))

    def open_import_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Add profiles")
        dialog.geometry("640x420")
        dialog.transient(self)
        dialog.grab_set()
        ttk.Label(dialog, text="Paste one or more vless://, trojan://, vmess://, or ss:// links.").pack(anchor=tk.W, padx=14, pady=(14, 4))
        text = tk.Text(dialog, wrap=tk.WORD, font=("Consolas", 10))
        text.pack(fill=tk.BOTH, expand=True, padx=14, pady=8)
        detected = tk.StringVar(value="0 links detected")
        ttk.Label(dialog, textvariable=detected, style="Subtle.TLabel").pack(anchor=tk.W, padx=14)

        def update_count(_event=None) -> None:
            count = count_candidates(text.get("1.0", tk.END))
            detected.set(f"{count} link{'s' if count != 1 else ''} detected")

        def submit() -> None:
            raw = text.get("1.0", tk.END)
            added, dupes, errors = self.add_profiles_from_text(raw)
            messagebox.showinfo("Import complete", f"Added {added}. Duplicates skipped {dupes}. Failed {len(errors)}.")
            dialog.destroy()

        text.bind("<KeyRelease>", update_count)
        buttons = ttk.Frame(dialog)
        buttons.pack(fill=tk.X, padx=14, pady=14)
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Add", command=submit).pack(side=tk.RIGHT, padx=(0, 8))

    def import_file(self) -> None:
        path = filedialog.askopenfilename(title="Import profiles", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        raw = Path(path).read_text(encoding="utf-8", errors="ignore")
        added, dupes, errors = self.add_profiles_from_text(raw)
        messagebox.showinfo("Import complete", f"Added {added}. Duplicates skipped {dupes}. Failed {len(errors)}.")

    def add_profiles_from_text(self, raw: str) -> tuple[int, int, list[str]]:
        parsed, errors = import_many(raw)
        added = 0
        dupes = 0
        for profile in parsed:
            if self.is_duplicate(profile):
                dupes += 1
                continue
            self.profiles.append(profile)
            added += 1
            if not self.settings.active_profile_id:
                self.settings.active_profile_id = profile.id
        self.store.save_profiles(self.profiles)
        self.store.save_settings(self.settings)
        self.refresh_all()
        return added, dupes, errors

    def is_duplicate(self, profile: Profile) -> bool:
        for existing in self.profiles:
            if (
                existing.kind == profile.kind
                and existing.server == profile.server
                and existing.server_port == profile.server_port
                and existing.uuid == profile.uuid
                and existing.password == profile.password
                and existing.tls.server_name == profile.tls.server_name
            ):
                return True
        return False

    def rename_selected_profile(self) -> None:
        selected = self.selected_profiles()
        if len(selected) != 1:
            messagebox.showinfo("Rename", "Select one profile to rename.")
            return
        profile = selected[0]
        dialog = tk.Toplevel(self)
        dialog.title("Rename profile")
        dialog.transient(self)
        dialog.grab_set()
        value = tk.StringVar(value=profile.name)
        ttk.Entry(dialog, textvariable=value, width=48).pack(padx=16, pady=16)

        def save() -> None:
            name = value.get().strip()
            if name:
                profile.name = name
                self.store.save_profiles(self.profiles)
                self.refresh_all()
            dialog.destroy()

        ttk.Button(dialog, text="Save", command=save).pack(pady=(0, 16))

    def delete_selected_profiles(self) -> None:
        selected = self.selected_profiles()
        if not selected:
            return
        if not messagebox.askyesno("Delete profiles", f"Delete {len(selected)} selected profile(s)?"):
            return
        ids = {profile.id for profile in selected}
        self.profiles = [profile for profile in self.profiles if profile.id not in ids]
        for profile_id in ids:
            self.ping_results.pop(profile_id, None)
        if self.settings.active_profile_id in ids:
            self.settings.active_profile_id = self.profiles[0].id if self.profiles else None
        self.store.save_profiles(self.profiles)
        self.store.save_ping_results(self.ping_results)
        self.store.save_settings(self.settings)
        self.refresh_all()

    def export_selected_profiles(self) -> None:
        selected = self.selected_profiles()
        if not selected:
            messagebox.showinfo("Export", "Select one or more profiles to export.")
            return
        try:
            text = export_text(selected)
        except ProfileExportError as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        path = filedialog.asksaveasfilename(title="Export profiles", defaultextension=".txt", filetypes=[("Text files", "*.txt")])
        if not path:
            return
        Path(path).write_text(text, encoding="utf-8")

    def ping_all_profiles(self) -> None:
        if not self.profiles:
            return
        if not self.save_settings_from_controls(show_success=False):
            return
        self.ping_cancel.clear()
        self.ping_button.configure(state=tk.DISABLED)
        self.controller.set_settings(self.settings, self.listener_config)
        self.controller.ping_profiles_async(
            list(self.profiles),
            on_result=lambda profile_id, result: self.post("ping_result", profile_id, result),
            on_done=lambda: self.post("ping_done"),
            cancel_event=self.ping_cancel,
        )

    def cancel_ping(self) -> None:
        self.ping_cancel.set()

    def remove_no_ping(self) -> None:
        doomed = [profile for profile in self.profiles if self.ping_results.get(profile.id, PingResult()).millis is None]
        if not doomed:
            return
        if not messagebox.askyesno("Remove no ping", f"Remove {len(doomed)} profile(s) without a successful ping?"):
            return
        doomed_ids = {profile.id for profile in doomed}
        self.profiles = [profile for profile in self.profiles if profile.id not in doomed_ids]
        for profile_id in doomed_ids:
            self.ping_results.pop(profile_id, None)
        self.store.save_profiles(self.profiles)
        self.store.save_ping_results(self.ping_results)
        self.refresh_all()

    def relaunch_admin(self) -> None:
        try:
            relaunch_as_admin(Path(__file__).resolve())
            self.destroy()
        except Exception as exc:
            messagebox.showerror("Relaunch failed", str(exc))

    def restart_connection(self) -> None:
        active = self.active_profile()
        if not active:
            return

        def after_stop(_ok: bool, _message: str) -> None:
            self.controller.set_settings(self.settings, self.listener_config)
            self.controller.start_async(active, lambda ok, msg: self.post("start_done", ok, msg))

        self.controller.stop_async(after_stop)

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    stream, text = payload
                    self.append_log(stream, text)
                elif kind == "state":
                    self.refresh_status()
                elif kind == "start_done":
                    ok, message = payload
                    self.refresh_status()
                    if ok:
                        self.after(900, self.refresh_egress)
                    else:
                        messagebox.showerror("Connect failed", message)
                elif kind == "stop_done":
                    ok, message = payload
                    self.refresh_status()
                    self.egress_var.set("Not resolved")
                    if not ok:
                        messagebox.showerror("Disconnect failed", message)
                elif kind == "egress":
                    ok, text, country = payload
                    self.egress_var.set(f"{text} {country or ''}".strip() if ok else text)
                elif kind == "ping_result":
                    profile_id, result = payload
                    self.ping_results[profile_id] = result
                    self.store.save_ping_results(self.ping_results)
                    self.refresh_profile_tree()
                elif kind == "ping_done":
                    self.ping_button.configure(state=tk.NORMAL)
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def append_log(self, stream: str, text: str) -> None:
        if not self.settings.logs_enabled:
            return
        prefix = "" if stream == "stdout" else ""
        self.log_text.insert(tk.END, prefix + text)
        self.log_text.see(tk.END)

    def _tick(self) -> None:
        self.controller.sample_bandwidth()
        self.down_var.set(rate(self.controller.download_bps))
        self.up_var.set(rate(self.controller.upload_bps))
        self.total_var.set(format_bytes(self.controller.session_down + self.controller.session_up))
        if self.controller.started_at:
            elapsed = int(time.time() - self.controller.started_at)
            self.uptime_var.set(format_duration(elapsed))
        else:
            self.uptime_var.set("0s")
        self.after(1000, self._tick)

    def _ensure_window_visible(self) -> None:
        try:
            if self.state() == "withdrawn":
                self.deiconify()
        finally:
            if self.winfo_exists():
                self.after(3000, self._ensure_window_visible)

    def _profile_label(self, profile: Profile | None) -> str:
        if profile is None:
            return ""
        return f"{profile.name} ({profile.display_kind})"

    def on_close(self) -> None:
        if self.controller.is_running and not messagebox.askyesno("Quit Cloak", "Disconnect and quit?"):
            return
        try:
            self.controller.stop()
        except Exception:
            pass
        self.destroy()


def rate(bytes_per_second: float) -> str:
    if bytes_per_second < 1000:
        return "0 KB/s"
    if bytes_per_second < 1_000_000:
        return f"{bytes_per_second / 1000:.0f} KB/s"
    if bytes_per_second < 1_000_000_000:
        return f"{bytes_per_second / 1_000_000:.1f} MB/s"
    return f"{bytes_per_second / 1_000_000_000:.2f} GB/s"


def format_bytes(value: int) -> str:
    if value < 1000:
        return f"{value} B"
    if value < 1_000_000:
        return f"{value / 1000:.1f} KB"
    if value < 1_000_000_000:
        return f"{value / 1_000_000:.2f} MB"
    return f"{value / 1_000_000_000:.2f} GB"


def format_duration(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


if __name__ == "__main__":
    CloakWindowsApp().mainloop()
