from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import sv_ttk

try:
    import pywinstyles  # type: ignore
except Exception:  # pragma: no cover - non-Windows or missing dep
    pywinstyles = None

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


PALETTE_DARK = {
    "bg":              "#1c1c1c",
    "card":            "#2b2b2b",
    "card_alt":        "#373737",
    "text":            "#ffffff",
    "subtle":          "#9d9d9d",
    "border":          "#454545",
    "accent":          "#57c8ff",
    "accent_fg":       "#0b1117",
    "success":         "#6ccb5f",
    "danger":          "#ff99a4",
    "warn":            "#f0b86b",
    "pill_success_bg": "#1d3829",
    "pill_success_fg": "#9ddf94",
    "pill_warn_bg":    "#3d2c0a",
    "pill_warn_fg":    "#f0c674",
    "pill_subtle_bg":  "#3a3a3a",
    "pill_subtle_fg":  "#cdcdcd",
    "pill_danger_bg":  "#3a1d1d",
    "pill_danger_fg":  "#ff99a4",
    "ping_good":       "#6ccb5f",
    "ping_okay":       "#f0b86b",
    "ping_bad":        "#ff99a4",
    "ping_dead":       "#7e7e7e",
}

PALETTE_LIGHT = {
    "bg":              "#f3f3f3",
    "card":            "#ffffff",
    "card_alt":        "#f9f9f9",
    "text":            "#1a1a1a",
    "subtle":          "#5d5d5d",
    "border":          "#d1d1d1",
    "accent":          "#005fb8",
    "accent_fg":       "#ffffff",
    "success":         "#107c10",
    "danger":          "#c42b1c",
    "warn":            "#9e7800",
    "pill_success_bg": "#dcf4dc",
    "pill_success_fg": "#0b6a0b",
    "pill_warn_bg":    "#fff4ce",
    "pill_warn_fg":    "#7d5a00",
    "pill_subtle_bg":  "#ededed",
    "pill_subtle_fg":  "#454545",
    "pill_danger_bg":  "#fde7e9",
    "pill_danger_fg":  "#a52d22",
    "ping_good":       "#107c10",
    "ping_okay":       "#9e7800",
    "ping_bad":        "#c42b1c",
    "ping_dead":       "#8a8a8a",
}


# Segoe Fluent Icons / Segoe MDL2 Assets — installed by default on Win10/11.
# When the font isn't available the codepoints render as boxes; we fall back to
# plain text labels on the buttons either way.
ICON_POWER     = ""
ICON_DOWN      = ""
ICON_UP        = ""
ICON_SIGMA     = ""
ICON_CLOCK     = ""
ICON_GLOBE     = ""
ICON_INFO      = ""
ICON_SHIELD    = ""
ICON_SUN       = ""
ICON_MOON      = ""
ICON_SYSTEM    = ""
ICON_ADD       = ""
ICON_IMPORT    = ""
ICON_RENAME    = ""
ICON_EXPORT    = ""
ICON_DELETE    = ""
ICON_PING      = ""
ICON_CANCEL    = ""
ICON_BROOM     = ""
ICON_CLEAR     = ""
ICON_FOLDER    = ""
ICON_COPY      = ""
ICON_PAUSE     = ""
ICON_PLAY      = ""
ICON_REFRESH   = ""


class CloakWindowsApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Cloak for Windows")
        self.geometry("1080x740")
        self.minsize(960, 640)

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
        self._toast_after_id: str | None = None
        self._pulse_after_id: str | None = None
        self._pulse_phase = False
        self._autoscroll_paused = False
        self._themed_text_widgets: list[tk.Text] = []
        self._themed_canvases: list[tk.Canvas] = []
        self._theme_listeners: list = []

        self.controller = CloakController(
            self.store,
            self.settings,
            self.listener_config,
            emit=lambda stream, text: self.post("log", stream, text),
            state_changed=lambda: self.post("state"),
        )

        self._load_icon()
        self._init_fonts()
        self._create_vars()
        self._apply_theme(self.settings.appearance_mode, first=True)
        self._build_shell()
        self.refresh_all()
        self.after(120, self._drain_queue)
        self.after(1000, self._tick)
        self.after(3000, self._ensure_window_visible)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ── Infrastructure ──────────────────────────────────────────────────────

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

    def _init_fonts(self) -> None:
        families = set(tkfont.families())
        ui_family = next(
            (name for name in ("Segoe UI Variable", "Segoe UI") if name in families),
            "Segoe UI",
        )
        mono_family = next(
            (name for name in ("Cascadia Mono", "Cascadia Code", "Consolas") if name in families),
            "Consolas",
        )
        icon_family = next(
            (name for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if name in families),
            "Segoe UI Symbol",
        )
        self._fonts = {
            "ui":        (ui_family, 10),
            "ui_bold":   (ui_family, 10, "bold"),
            "ui_caption":(ui_family, 9),
            "ui_micro":  (ui_family, 8, "bold"),
            "ui_title":  (ui_family, 22, "bold"),
            "ui_status": (ui_family, 22, "bold"),
            "ui_metric": (ui_family, 17, "bold"),
            "ui_brand":  (ui_family, 13, "bold"),
            "mono":      (mono_family, 10),
            "icon":      (icon_family, 11),
            "icon_lg":   (icon_family, 14),
            "icon_xl":   (icon_family, 22),
        }

    # ── Theme ───────────────────────────────────────────────────────────────

    def _resolve_theme(self, mode: str) -> str:
        if mode == "light":
            return "light"
        if mode == "dark":
            return "dark"
        if sys.platform == "win32":
            try:
                import winreg

                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                )
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                winreg.CloseKey(key)
                return "light" if value else "dark"
            except OSError:
                pass
        return "dark"

    def _apply_theme(self, mode: str, *, first: bool = False) -> None:
        resolved = self._resolve_theme(mode)
        sv_ttk.set_theme(resolved)
        self._palette = PALETTE_DARK if resolved == "dark" else PALETTE_LIGHT
        self._configure_style_overlays()
        self._retint_themed_widgets()
        self._apply_window_chrome(resolved)
        if not first:
            self._notify_theme_listeners()

    def _apply_window_chrome(self, mode: str) -> None:
        if pywinstyles is None or sys.platform != "win32":
            return
        # Mica needs Windows 11; on 10 it falls back to plain titlebar colour.
        for style_name in ("mica", "normal"):
            try:
                pywinstyles.apply_style(self, style_name if style_name == "mica" else None)
                break
            except Exception:
                continue
        try:
            pywinstyles.change_header_color(
                self, "#1c1c1c" if mode == "dark" else "#f3f3f3"
            )
            pywinstyles.change_title_color(
                self, "#ffffff" if mode == "dark" else "#1a1a1a"
            )
        except Exception:
            pass

    def _configure_style_overlays(self) -> None:
        p = self._palette
        style = ttk.Style(self)

        # Accent + danger button variants — sv-ttk already ships Accent.TButton;
        # we re-register to tune padding/font so the hero Connect button reads big.
        style.configure(
            "Hero.Accent.TButton",
            font=(self._fonts["ui_bold"][0], 12, "bold"),
            padding=(26, 11),
        )
        style.configure(
            "Hero.Danger.TButton",
            font=(self._fonts["ui_bold"][0], 12, "bold"),
            padding=(26, 11),
        )
        style.map(
            "Hero.Danger.TButton",
            foreground=[("!disabled", "#ffffff")],
            background=[
                ("pressed", "#7f1d1d"),
                ("active", "#b91c1c"),
                ("!disabled", "#c42b1c"),
            ],
        )

        # Pills (small rounded labels)
        for variant in ("Success", "Warn", "Subtle", "Danger", "Accent"):
            key = variant.lower()
            if variant == "Accent":
                bg = p["accent"]
                fg = p["accent_fg"]
            else:
                bg = p[f"pill_{key}_bg"]
                fg = p[f"pill_{key}_fg"]
            style.configure(
                f"Pill.{variant}.TLabel",
                background=bg,
                foreground=fg,
                font=(self._fonts["ui_micro"][0], 8, "bold"),
                padding=(8, 3),
            )

        # Brand + title typography
        style.configure(
            "Brand.TLabel",
            font=self._fonts["ui_brand"],
            foreground=p["text"],
        )
        style.configure(
            "Title.TLabel",
            font=self._fonts["ui_title"],
            foreground=p["text"],
        )
        style.configure(
            "Status.TLabel",
            font=self._fonts["ui_status"],
            foreground=p["text"],
        )
        style.configure(
            "Subtle.TLabel",
            font=self._fonts["ui"],
            foreground=p["subtle"],
        )
        style.configure(
            "Caption.TLabel",
            font=self._fonts["ui_caption"],
            foreground=p["subtle"],
        )
        style.configure(
            "Section.TLabel",
            font=self._fonts["ui_micro"],
            foreground=p["subtle"],
        )
        style.configure(
            "Metric.TLabel",
            font=self._fonts["ui_metric"],
            foreground=p["text"],
        )
        style.configure(
            "MetricCaption.TLabel",
            font=self._fonts["ui_caption"],
            foreground=p["subtle"],
        )
        style.configure(
            "Icon.TLabel",
            font=self._fonts["icon_lg"],
            foreground=p["subtle"],
        )
        style.configure(
            "IconHero.TLabel",
            font=self._fonts["icon_xl"],
            foreground=p["accent"],
        )

        # Compact secondary buttons used in toolbars (icon + label)
        style.configure(
            "Toolbar.TButton",
            font=self._fonts["ui"],
            padding=(10, 6),
        )

        # Toast label
        style.configure(
            "Toast.TLabel",
            background=p["pill_subtle_bg"],
            foreground=p["pill_subtle_fg"],
            font=self._fonts["ui_caption"],
            padding=(12, 6),
        )

        # Treeview row tinting for ping levels
        style.configure(
            "Treeview",
            rowheight=34,
            font=self._fonts["ui"],
        )
        style.configure(
            "Treeview.Heading",
            font=self._fonts["ui_micro"],
        )

    def _retint_themed_widgets(self) -> None:
        p = self._palette
        card_bg = self._lookup_card_bg()
        for widget in list(self._themed_text_widgets):
            try:
                widget.configure(
                    background=p["card_alt"],
                    foreground=p["text"],
                    insertbackground=p["text"],
                    selectbackground=p["accent"],
                    selectforeground=p["accent_fg"],
                )
            except tk.TclError:
                self._themed_text_widgets.remove(widget)
        for canvas in list(self._themed_canvases):
            try:
                canvas.configure(background=card_bg)
            except tk.TclError:
                self._themed_canvases.remove(canvas)
        if hasattr(self, "log_text"):
            try:
                self.log_text.tag_configure("stderr", foreground=p["danger"])
                self.log_text.tag_configure("stdout", foreground=p["text"])
            except tk.TclError:
                pass
        if hasattr(self, "profile_tree"):
            try:
                self.profile_tree.tag_configure("ping_good", foreground=p["ping_good"])
                self.profile_tree.tag_configure("ping_okay", foreground=p["ping_okay"])
                self.profile_tree.tag_configure("ping_bad", foreground=p["ping_bad"])
                self.profile_tree.tag_configure("ping_dead", foreground=p["ping_dead"])
            except tk.TclError:
                pass
        if hasattr(self, "_status_dot"):
            self._refresh_status_dot()
        if hasattr(self, "_theme_toggle_button"):
            self._refresh_theme_toggle_icon()

    def _lookup_card_bg(self) -> str:
        style = ttk.Style(self)
        for name in ("Card.TFrame", "TFrame"):
            value = style.lookup(name, "background")
            if value:
                return value
        return self._palette["card"]

    def _notify_theme_listeners(self) -> None:
        for callback in self._theme_listeners:
            try:
                callback()
            except Exception:
                pass

    # ── State variables ─────────────────────────────────────────────────────

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
        self.toast_var = tk.StringVar(value="")
        self.profile_count_var = tk.StringVar(value="0 profiles")

    # ── Shell ───────────────────────────────────────────────────────────────

    def _build_shell(self) -> None:
        self._build_top_bar()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=14, pady=(8, 6))

        self.dashboard_tab = ttk.Frame(self.notebook)
        self.profiles_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        self.logs_tab = ttk.Frame(self.notebook)
        self.about_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.dashboard_tab, text="  Dashboard  ")
        self.notebook.add(self.profiles_tab, text="  Profiles  ")
        self.notebook.add(self.settings_tab, text="  Settings  ")
        self.notebook.add(self.logs_tab, text="  Logs  ")
        self.notebook.add(self.about_tab, text="  About  ")

        self._build_dashboard()
        self._build_profiles()
        self._build_settings()
        self._build_logs()
        self._build_about()
        self._build_toast()

    def _build_top_bar(self) -> None:
        bar = ttk.Frame(self, padding=(18, 12, 14, 8))
        bar.pack(fill=tk.X)

        brand_wrap = ttk.Frame(bar)
        brand_wrap.pack(side=tk.LEFT)
        ttk.Label(brand_wrap, text=ICON_SHIELD, style="IconHero.TLabel").pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Label(brand_wrap, text="Cloak", style="Brand.TLabel").pack(side=tk.LEFT)
        ttk.Label(brand_wrap, text="for Windows", style="Caption.TLabel").pack(
            side=tk.LEFT, padx=(8, 0), pady=(6, 0)
        )

        right = ttk.Frame(bar)
        right.pack(side=tk.RIGHT)

        if is_admin():
            self.admin_pill = ttk.Label(
                right,
                text="ADMINISTRATOR",
                style="Pill.Success.TLabel",
            )
        else:
            self.admin_pill = ttk.Label(
                right,
                text="STANDARD USER  •  CLICK TO ELEVATE",
                style="Pill.Warn.TLabel",
                cursor="hand2",
            )
            self.admin_pill.bind("<Button-1>", lambda _e: self.relaunch_admin())
        self.admin_pill.pack(side=tk.RIGHT, padx=(8, 0))

        self._theme_toggle_button = ttk.Button(
            right,
            text=ICON_MOON,
            style="Toolbar.TButton",
            width=3,
            command=self._cycle_appearance_mode,
        )
        self._theme_toggle_button.pack(side=tk.RIGHT, padx=(6, 0))
        # The toggle uses the icon font directly for the glyph.
        self._theme_toggle_button.configure(takefocus=False)
        self._refresh_theme_toggle_icon()

    def _refresh_theme_toggle_icon(self) -> None:
        mode = self.settings.appearance_mode
        glyph = {"light": ICON_SUN, "dark": ICON_MOON, "system": ICON_SYSTEM}.get(
            mode, ICON_SYSTEM
        )
        ttk.Style(self).configure(
            "ThemeToggle.Toolbar.TButton",
            font=self._fonts["icon"],
            padding=(8, 6),
        )
        try:
            self._theme_toggle_button.configure(
                text=glyph,
                style="ThemeToggle.Toolbar.TButton",
            )
        except tk.TclError:
            pass

    def _cycle_appearance_mode(self) -> None:
        order = ["system", "light", "dark"]
        try:
            index = order.index(self.settings.appearance_mode)
        except ValueError:
            index = 0
        new_mode = order[(index + 1) % len(order)]
        self.appearance_var.set(new_mode)
        self.settings.appearance_mode = new_mode
        self.store.save_settings(self.settings)
        self._apply_theme(new_mode)
        self._show_toast(f"Appearance set to {new_mode.title()}")

    # ── Dashboard ───────────────────────────────────────────────────────────

    def _build_dashboard(self) -> None:
        p = self._palette
        body = ttk.Frame(self.dashboard_tab, padding=(8, 10, 8, 8))
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)

        # ── Hero card ──
        hero = ttk.Frame(body, style="Card.TFrame", padding=(22, 20))
        hero.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        hero.columnconfigure(0, weight=1)

        left = ttk.Frame(hero, style="Card.TFrame")
        left.grid(row=0, column=0, sticky="ew")
        left.columnconfigure(1, weight=1)

        dot_canvas_bg = self._lookup_card_bg()
        self._status_dot = tk.Canvas(
            left,
            width=18,
            height=18,
            highlightthickness=0,
            bd=0,
            background=dot_canvas_bg,
        )
        self._status_dot.create_oval(2, 2, 16, 16, fill=p["subtle"], outline="", tags="dot")
        self._status_dot.grid(row=0, column=0, sticky="w", padx=(0, 12), pady=(6, 0))
        self._themed_canvases.append(self._status_dot)

        ttk.Label(left, textvariable=self.status_var, style="Status.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(
            left, textvariable=self.secondary_var, style="Subtle.TLabel"
        ).grid(row=1, column=1, sticky="w", pady=(2, 14))

        chooser = ttk.Frame(left, style="Card.TFrame")
        chooser.grid(row=2, column=1, sticky="ew")
        ttk.Label(chooser, text="ACTIVE PROFILE", style="Section.TLabel").pack(
            side=tk.LEFT, padx=(0, 10), pady=(4, 0)
        )
        self.active_combo = ttk.Combobox(
            chooser, textvariable=self.active_profile_var, state="readonly", width=44
        )
        self.active_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.active_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self.on_active_profile_changed()
        )

        right = ttk.Frame(hero, style="Card.TFrame")
        right.grid(row=0, column=1, sticky="e", padx=(20, 0))
        self.connect_button = ttk.Button(
            right,
            text=f"{ICON_POWER}  Connect",
            style="Hero.Accent.TButton",
            command=self.on_connect_clicked,
        )
        self.connect_button.pack(pady=(2, 0))

        # ── Routing card ──
        routing = ttk.LabelFrame(body, text="Routing", padding=(18, 12))
        routing.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        routing.columnconfigure(99, weight=1)

        self._proxy_radio = ttk.Radiobutton(
            routing, text="Proxy", variable=self.mode_var, value="proxy",
            command=self.on_settings_changed,
        )
        self._proxy_radio.grid(row=0, column=0, sticky="w")

        self._tunnel_radio = ttk.Radiobutton(
            routing, text="Tunnel", variable=self.mode_var, value="tunnel",
            command=self.on_settings_changed, state="disabled",
        )
        self._tunnel_radio.grid(row=0, column=1, sticky="w", padx=(18, 6))
        ttk.Label(routing, text="COMING SOON", style="Pill.Subtle.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 18)
        )

        ttk.Checkbutton(
            routing,
            text="Use Windows system proxy",
            variable=self.use_proxy_var,
            command=self.on_settings_changed,
        ).grid(row=0, column=3, sticky="w")

        ttk.Label(routing, textvariable=self.endpoint_var, style="Caption.TLabel").grid(
            row=0, column=99, sticky="e"
        )

        # ── Metrics card ──
        metrics = ttk.LabelFrame(body, text="Session metrics", padding=(18, 12))
        metrics.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        for column in range(5):
            metrics.columnconfigure(column, weight=1, uniform="metrics")

        tiles = (
            (ICON_DOWN,  "Download", self.down_var),
            (ICON_UP,    "Upload",   self.up_var),
            (ICON_SIGMA, "Total",    self.total_var),
            (ICON_CLOCK, "Uptime",   self.uptime_var),
            (ICON_GLOBE, "Egress",   self.egress_var),
        )
        for column, (icon, title, var) in enumerate(tiles):
            tile = ttk.Frame(metrics)
            tile.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
            header = ttk.Frame(tile)
            header.pack(anchor="w")
            ttk.Label(header, text=icon, style="Icon.TLabel").pack(side=tk.LEFT, padx=(0, 6))
            ttk.Label(header, text=title.upper(), style="MetricCaption.TLabel").pack(side=tk.LEFT)
            ttk.Label(tile, textvariable=var, style="Metric.TLabel").pack(anchor="w", pady=(4, 0))

        ttk.Button(
            metrics, text=f"{ICON_REFRESH}  Refresh egress",
            style="Toolbar.TButton", command=self.refresh_egress,
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(14, 0))

        # ── Notes card ──
        notes = ttk.LabelFrame(body, text="About this build", padding=(18, 12))
        notes.grid(row=3, column=0, sticky="ew")
        notes.columnconfigure(1, weight=1)

        ttk.Label(notes, text=ICON_INFO, style="Icon.TLabel").grid(
            row=0, column=0, sticky="nw", padx=(0, 10), pady=(2, 0)
        )
        ttk.Label(
            notes,
            text=(
                "Proxy mode mirrors Cloak's macOS system-proxy path. The packet "
                "listener uses WinDivert via pydivert, so the app must run as "
                "Administrator. Tunnel mode is shown for parity with macOS, but "
                "needs a WinTun/tun2socks helper before it can route all traffic."
            ),
            wraplength=820,
            justify=tk.LEFT,
            style="Subtle.TLabel",
        ).grid(row=0, column=1, sticky="ew")

    # ── Profiles ────────────────────────────────────────────────────────────

    def _build_profiles(self) -> None:
        body = ttk.Frame(self.profiles_tab, padding=(8, 10, 8, 10))
        body.pack(fill=tk.BOTH, expand=True)

        toolbar = ttk.Frame(body)
        toolbar.pack(fill=tk.X, pady=(0, 10))

        primary = (
            (ICON_ADD,    "Add",         self.open_import_dialog),
            (ICON_IMPORT, "Import file", self.import_file),
            (ICON_RENAME, "Rename",      self.rename_selected_profile),
            (ICON_EXPORT, "Export",      self.export_selected_profiles),
            (ICON_DELETE, "Delete",      self.delete_selected_profiles),
        )
        for icon, label, cmd in primary:
            ttk.Button(
                toolbar, text=f"{icon}  {label}", style="Toolbar.TButton", command=cmd,
            ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Separator(toolbar, orient="vertical").pack(
            side=tk.LEFT, fill=tk.Y, padx=10, pady=4
        )

        self.ping_button = ttk.Button(
            toolbar, text=f"{ICON_PING}  Ping all",
            style="Toolbar.TButton", command=self.ping_all_profiles,
        )
        self.ping_button.pack(side=tk.LEFT, padx=(0, 6))
        self.cancel_ping_button = ttk.Button(
            toolbar, text=f"{ICON_CANCEL}  Cancel ping",
            style="Toolbar.TButton", command=self.cancel_ping,
        )
        self.cancel_ping_button.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            toolbar, text=f"{ICON_BROOM}  Remove no-ping",
            style="Toolbar.TButton", command=self.remove_no_ping,
        ).pack(side=tk.LEFT)

        ttk.Label(toolbar, textvariable=self.profile_count_var, style="Caption.TLabel").pack(
            side=tk.RIGHT
        )

        # Container holds either the treeview or the empty state.
        self._profile_container = ttk.Frame(body)
        self._profile_container.pack(fill=tk.BOTH, expand=True)

        self._profile_tree_frame = ttk.Frame(self._profile_container)
        columns = ("active", "name", "kind", "endpoint", "sni", "ping")
        self.profile_tree = ttk.Treeview(
            self._profile_tree_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        vsb = ttk.Scrollbar(
            self._profile_tree_frame, orient="vertical", command=self.profile_tree.yview
        )
        self.profile_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.profile_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        headings = {
            "active": "",
            "name": "Name",
            "kind": "Protocol",
            "endpoint": "Server",
            "sni": "SNI / Host",
            "ping": "Ping",
        }
        widths = {
            "active": 40, "name": 240, "kind": 110,
            "endpoint": 200, "sni": 260, "ping": 90,
        }
        for col in columns:
            self.profile_tree.heading(col, text=headings[col])
            anchor = tk.CENTER if col == "active" else tk.W
            self.profile_tree.column(col, width=widths[col], anchor=anchor,
                                     stretch=col in ("name", "sni"))
        self.profile_tree.bind("<Double-1>", lambda _e: self.activate_selected_profile())

        self._profile_empty = ttk.Frame(self._profile_container, padding=(40, 60))
        empty_inner = ttk.Frame(self._profile_empty)
        empty_inner.place(relx=0.5, rely=0.45, anchor="center")
        ttk.Label(empty_inner, text=ICON_GLOBE, font=self._fonts["icon_xl"],
                  style="Subtle.TLabel").pack(pady=(0, 8))
        ttk.Label(empty_inner, text="No profiles yet",
                  style="Title.TLabel").pack(pady=(0, 6))
        ttk.Label(
            empty_inner,
            text="Paste a vless://, trojan://, vmess://, or ss:// link to get started.",
            style="Subtle.TLabel",
        ).pack(pady=(0, 16))
        ttk.Button(
            empty_inner, text=f"{ICON_ADD}  Add a profile",
            style="Accent.TButton", command=self.open_import_dialog,
        ).pack()

    # ── Settings ────────────────────────────────────────────────────────────

    def _build_settings(self) -> None:
        outer = ttk.Frame(self.settings_tab, padding=(8, 10, 8, 10))
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=2)
        outer.columnconfigure(1, weight=1)

        # ── Cloudflare listener (col 0) ──
        cloud = ttk.LabelFrame(outer, text="Cloudflare listener JSON", padding=(14, 12))
        cloud.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        cloud.rowconfigure(0, weight=1)
        cloud.columnconfigure(0, weight=1)

        text_frame = ttk.Frame(cloud)
        text_frame.grid(row=0, column=0, sticky="nsew")
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        self.listener_text = tk.Text(
            text_frame,
            height=12, wrap=tk.NONE, undo=True,
            font=self._fonts["mono"],
            relief=tk.FLAT, borderwidth=0, padx=10, pady=8,
        )
        self.listener_text.grid(row=0, column=0, sticky="nsew")
        listener_vsb = ttk.Scrollbar(text_frame, orient="vertical",
                                     command=self.listener_text.yview)
        listener_vsb.grid(row=0, column=1, sticky="ns")
        self.listener_text.configure(yscrollcommand=listener_vsb.set)
        self.listener_text.insert("1.0", self.listener_config.encode_json())
        self._themed_text_widgets.append(self.listener_text)

        cloud_btns = ttk.Frame(cloud)
        cloud_btns.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(cloud_btns, text="Restore default",
                   style="Toolbar.TButton",
                   command=self.restore_listener_default).pack(side=tk.LEFT)
        ttk.Button(cloud_btns, text="Save settings",
                   style="Accent.TButton",
                   command=lambda: self.save_settings_from_controls(show_success=True)
                   ).pack(side=tk.RIGHT)

        # ── Local proxy listeners (col 1) ──
        proxy = ttk.LabelFrame(outer, text="Local proxy listeners", padding=(14, 12))
        proxy.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        proxy.columnconfigure(1, weight=1)

        ttk.Checkbutton(proxy, text="Expose to LAN", variable=self.lan_var,
                        command=self.on_lan_changed
                        ).grid(row=0, column=0, columnspan=2, sticky="w")

        for row, label, var in (
            (1, "SOCKS port", self.socks_port_var),
            (2, "HTTP port",  self.http_port_var),
        ):
            ttk.Label(proxy, text=label).grid(row=row, column=0, sticky="w",
                                              pady=(12, 0))
            ttk.Spinbox(proxy, from_=1, to=65535, textvariable=var,
                        width=10).grid(row=row, column=1, sticky="w",
                                       pady=(12, 0), padx=(10, 0))

        ttk.Label(proxy, text="xray log level").grid(row=3, column=0, sticky="w",
                                                     pady=(12, 0))
        ttk.Combobox(proxy, textvariable=self.log_level_var,
                     values=("trace", "debug", "info", "warn", "error"),
                     state="readonly", width=10
                     ).grid(row=3, column=1, sticky="w", pady=(12, 0), padx=(10, 0))

        ttk.Checkbutton(proxy, text="Capture logs", variable=self.logs_enabled_var,
                        command=self.on_settings_changed
                        ).grid(row=4, column=0, columnspan=2, sticky="w",
                               pady=(14, 0))

        # ── Runtime paths (row 1, full width) ──
        paths = ttk.LabelFrame(outer, text="Runtime paths", padding=(14, 12))
        paths.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        paths.columnconfigure(1, weight=1)

        ttk.Label(paths, text="xray.exe").grid(row=0, column=0, sticky="w")
        ttk.Entry(paths, textvariable=self.xray_path_var).grid(
            row=0, column=1, sticky="ew", padx=(12, 8))
        ttk.Button(paths, text="Browse", style="Toolbar.TButton",
                   command=self.browse_xray).grid(row=0, column=2)

        ttk.Label(paths, text="Python").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(paths, textvariable=self.python_path_var).grid(
            row=1, column=1, sticky="ew", padx=(12, 8), pady=(10, 0))
        ttk.Button(paths, text="Use current", style="Toolbar.TButton",
                   command=lambda: self.python_path_var.set("")
                   ).grid(row=1, column=2, pady=(10, 0))

        # ── Appearance (row 2, full width) ──
        appear = ttk.LabelFrame(outer, text="Appearance", padding=(14, 12))
        appear.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        appear.columnconfigure(2, weight=1)

        ttk.Label(appear, text="Theme").grid(row=0, column=0, sticky="w")
        appearance_combo = ttk.Combobox(
            appear, textvariable=self.appearance_var,
            values=("system", "light", "dark"),
            state="readonly", width=12,
        )
        appearance_combo.grid(row=0, column=1, sticky="w", padx=(10, 14))
        appearance_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self.on_appearance_changed()
        )
        ttk.Label(
            appear,
            text="Choose Light, Dark, or follow the Windows app theme.",
            style="Subtle.TLabel",
        ).grid(row=0, column=2, sticky="w")

    # ── Logs ────────────────────────────────────────────────────────────────

    def _build_logs(self) -> None:
        body = ttk.Frame(self.logs_tab, padding=(8, 10, 8, 10))
        body.pack(fill=tk.BOTH, expand=True)

        toolbar = ttk.Frame(body)
        toolbar.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(toolbar, text=f"{ICON_CLEAR}  Clear",
                   style="Toolbar.TButton",
                   command=lambda: self.log_text.delete("1.0", tk.END)
                   ).pack(side=tk.LEFT)
        ttk.Button(toolbar, text=f"{ICON_COPY}  Copy all",
                   style="Toolbar.TButton",
                   command=self._copy_logs).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(toolbar, text=f"{ICON_FOLDER}  Open app data",
                   style="Toolbar.TButton",
                   command=self.show_app_data).pack(side=tk.LEFT, padx=(6, 0))

        self._autoscroll_button = ttk.Button(
            toolbar, text=f"{ICON_PAUSE}  Pause autoscroll",
            style="Toolbar.TButton", command=self._toggle_autoscroll,
        )
        self._autoscroll_button.pack(side=tk.RIGHT)

        wrap = ttk.Frame(body)
        wrap.pack(fill=tk.BOTH, expand=True)
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            wrap, wrap=tk.WORD, font=self._fonts["mono"],
            relief=tk.FLAT, borderwidth=0, padx=12, pady=10,
            state=tk.NORMAL,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.log_text.yview)
        log_vsb.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_vsb.set)
        self._themed_text_widgets.append(self.log_text)
        # apply ping/log tags now that the widget exists
        self._retint_themed_widgets()

    def _toggle_autoscroll(self) -> None:
        self._autoscroll_paused = not self._autoscroll_paused
        if self._autoscroll_paused:
            self._autoscroll_button.configure(text=f"{ICON_PLAY}  Resume autoscroll")
        else:
            self._autoscroll_button.configure(text=f"{ICON_PAUSE}  Pause autoscroll")

    def _copy_logs(self) -> None:
        text = self.log_text.get("1.0", tk.END)
        self.clipboard_clear()
        self.clipboard_append(text)
        self._show_toast("Logs copied to clipboard")

    # ── About ───────────────────────────────────────────────────────────────

    def _build_about(self) -> None:
        body = ttk.Frame(self.about_tab, padding=(32, 28))
        body.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(body)
        header.pack(anchor="w")

        icon = ROOT_DIR / "assets" / "Cloak.png"
        if icon.exists():
            try:
                self._about_icon = tk.PhotoImage(file=str(icon))
                ttk.Label(header, image=self._about_icon).pack(side=tk.LEFT, padx=(0, 16))
            except tk.TclError:
                self._about_icon = None

        text_col = ttk.Frame(header)
        text_col.pack(side=tk.LEFT)
        ttk.Label(text_col, text="Cloak for Windows", style="Title.TLabel").pack(anchor="w")
        ttk.Label(text_col, text="GPL-3.0 • port of g3ntrix/Cloak",
                  style="Caption.TLabel").pack(anchor="w", pady=(2, 0))

        ttk.Separator(body, orient="horizontal").pack(fill=tk.X, pady=(20, 18))

        ttk.Label(
            body,
            text=(
                "A Windows port of g3ntrix/Cloak — a macOS SNI-spoofing proxy client.\n\n"
                "Implemented here: profile library, VLESS / Trojan / VMess / Shadowsocks "
                "import, real curl-based ping, Xray config generation, Python SNI bridge "
                "process control, Windows system-proxy toggling, egress lookup, logs, "
                "and persisted settings."
            ),
            style="Subtle.TLabel",
            wraplength=720,
            justify=tk.LEFT,
        ).pack(anchor="w")

    # ── Toast ───────────────────────────────────────────────────────────────

    def _build_toast(self) -> None:
        self._toast_label = ttk.Label(self, textvariable=self.toast_var, style="Toast.TLabel")
        # Not packed until needed.

    def _show_toast(self, message: str, duration_ms: int = 2200) -> None:
        if self._toast_after_id:
            try:
                self.after_cancel(self._toast_after_id)
            except Exception:
                pass
        self.toast_var.set(message)
        try:
            self._toast_label.place(relx=1.0, rely=1.0, x=-22, y=-18, anchor="se")
            self._toast_label.lift()
        except tk.TclError:
            return
        self._toast_after_id = self.after(duration_ms, self._hide_toast)

    def _hide_toast(self) -> None:
        try:
            self._toast_label.place_forget()
        except tk.TclError:
            pass
        self._toast_after_id = None

    # ── Refresh helpers ─────────────────────────────────────────────────────

    def refresh_all(self) -> None:
        self.refresh_active_combo()
        self.refresh_profile_tree()
        self.refresh_status()
        host = self.settings.resolved_socks_host_for_local_client
        self.endpoint_var.set(
            f"SOCKS  {host}:{self.settings.listen_port}   ·   "
            f"HTTP  {host}:{self.settings.http_port}"
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
        count = len(self.profiles)
        self.profile_count_var.set(f"{count} profile{'s' if count != 1 else ''}")

        # Toggle empty state
        if not self.profiles:
            self._profile_tree_frame.pack_forget()
            self._profile_empty.pack(fill=tk.BOTH, expand=True)
            self.profile_tree.delete(*self.profile_tree.get_children())
            return
        self._profile_empty.pack_forget()
        if not self._profile_tree_frame.winfo_ismapped():
            self._profile_tree_frame.pack(fill=tk.BOTH, expand=True)

        self.profile_tree.delete(*self.profile_tree.get_children())
        for profile in self.profiles:
            result = self.ping_results.get(profile.id)
            if result and result.millis is not None:
                ping_label = f"{result.millis} ms"
                if result.millis < 100:
                    ping_tag = "ping_good"
                elif result.millis < 250:
                    ping_tag = "ping_okay"
                else:
                    ping_tag = "ping_bad"
            elif result:
                ping_label = result.error or "failed"
                ping_tag = "ping_bad"
            else:
                ping_label = ""
                ping_tag = "ping_dead"
            self.profile_tree.insert(
                "",
                tk.END,
                iid=profile.id,
                tags=(ping_tag,),
                values=(
                    "●" if self.settings.active_profile_id == profile.id else "",
                    profile.name,
                    profile.display_kind,
                    f"{profile.server}:{profile.server_port}",
                    profile.subtitle,
                    ping_label,
                ),
            )

    def _refresh_status_dot(self) -> None:
        if not hasattr(self, "_status_dot"):
            return
        p = self._palette
        status = self.controller.status
        color = {
            "running":  p["success"],
            "starting": p["warn"],
            "stopping": p["warn"],
            "error":    p["danger"],
        }.get(status, p["subtle"])
        try:
            self._status_dot.itemconfig("dot", fill=color)
        except tk.TclError:
            pass

    def _pulse_status_dot(self) -> None:
        if self.controller.status not in ("starting", "stopping"):
            self._pulse_after_id = None
            self._refresh_status_dot()
            return
        p = self._palette
        self._pulse_phase = not self._pulse_phase
        color = p["warn"] if self._pulse_phase else p["pill_warn_bg"]
        try:
            self._status_dot.itemconfig("dot", fill=color)
        except tk.TclError:
            return
        self._pulse_after_id = self.after(550, self._pulse_status_dot)

    def refresh_status(self) -> None:
        status = self.controller.status
        self.status_var.set(self.controller.status_message)
        active = self.active_profile()
        if status == "running":
            self.connect_button.configure(
                text=f"{ICON_POWER}  Disconnect",
                state=tk.NORMAL, style="Hero.Danger.TButton",
            )
            self.secondary_var.set(f"Profile: {active.name if active else 'none'}")
        elif status in ("starting", "stopping"):
            self.connect_button.configure(
                text=f"{ICON_POWER}  Please wait",
                state=tk.DISABLED, style="Hero.Accent.TButton",
            )
            self.secondary_var.set(self.controller.status_message)
        elif status == "error":
            self.connect_button.configure(
                text=f"{ICON_POWER}  Connect",
                state=tk.NORMAL, style="Hero.Accent.TButton",
            )
            self.secondary_var.set(self.controller.status_message)
        else:
            self.connect_button.configure(
                text=f"{ICON_POWER}  Connect",
                state=tk.NORMAL, style="Hero.Accent.TButton",
            )
            self.secondary_var.set(
                "No profile selected. Import or pick one from Profiles."
                if not active else f"Profile: {active.name}"
            )

        self._refresh_status_dot()
        if status in ("starting", "stopping") and self._pulse_after_id is None:
            self._pulse_after_id = self.after(120, self._pulse_status_dot)

    # ── Profile actions ─────────────────────────────────────────────────────

    def active_profile(self) -> Profile | None:
        if not self.settings.active_profile_id:
            return self.profiles[0] if self.profiles else None
        return next(
            (profile for profile in self.profiles if profile.id == self.settings.active_profile_id),
            None,
        )

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
        self.controller.refresh_egress_async(
            lambda ok, text, country: self.post("egress", ok, text, country)
        )

    def on_appearance_changed(self) -> None:
        mode = self.appearance_var.get()
        self.settings.appearance_mode = mode
        self.store.save_settings(self.settings)
        self._apply_theme(mode)

    def on_settings_changed(self) -> None:
        previous_mode = self.settings.connection_mode
        previous_proxy = self.settings.use_system_proxy
        # Tunnel is shown as a disabled placeholder; reject any attempt to set it.
        if self.mode_var.get() == "tunnel":
            self.mode_var.set("proxy")
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
            socks = int(str(self.socks_port_var.get()).strip())
            http = int(str(self.http_port_var.get()).strip())
            if not 0 < socks <= 65535 or not 0 < http <= 65535:
                raise ValueError("Ports must be between 1 and 65535.")
            listener = ListenerProjectConfig.decode(self.listener_text.get("1.0", tk.END))
        except Exception as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return False
        self.settings.listen_port = socks
        self.settings.http_port = http
        self.settings.set_exposes_to_lan(self.lan_var.get())
        if self.mode_var.get() == "tunnel":
            self.mode_var.set("proxy")
        self.settings.connection_mode = self.mode_var.get()
        self.settings.use_system_proxy = self.use_proxy_var.get()
        self.settings.log_level = self.log_level_var.get()
        self.settings.logs_enabled = self.logs_enabled_var.get()
        self.settings.appearance_mode = self.appearance_var.get()
        self.settings.xray_path = (
            "" if self.xray_path_var.get().strip() == str(DEFAULT_XRAY)
            else self.xray_path_var.get().strip()
        )
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
            self._show_toast("Settings saved")
        return True

    def restore_listener_default(self) -> None:
        self.listener_text.delete("1.0", tk.END)
        self.listener_text.insert("1.0", ListenerProjectConfig.default_json())

    def browse_xray(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose xray.exe",
            filetypes=[("xray.exe", "xray.exe"), ("Executable", "*.exe"),
                       ("All files", "*.*")],
        )
        if path:
            self.xray_path_var.set(path)

    def show_app_data(self) -> None:
        messagebox.showinfo("App data", str(self.store.app_dir))

    # ── Dialogs ─────────────────────────────────────────────────────────────

    def _prepare_dialog(self, dialog: tk.Toplevel) -> None:
        dialog.configure(background=self._palette["bg"])
        dialog.transient(self)
        dialog.grab_set()
        if pywinstyles is not None and sys.platform == "win32":
            try:
                mode = sv_ttk.get_theme()
                pywinstyles.change_header_color(
                    dialog, "#1c1c1c" if mode == "dark" else "#f3f3f3"
                )
                pywinstyles.change_title_color(
                    dialog, "#ffffff" if mode == "dark" else "#1a1a1a"
                )
            except Exception:
                pass

    def open_import_dialog(self) -> None:
        p = self._palette
        dialog = tk.Toplevel(self)
        dialog.title("Add profiles")
        dialog.geometry("680x500")
        self._prepare_dialog(dialog)

        body = ttk.Frame(dialog, padding=(20, 18))
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            body,
            text="Paste one or more vless://, trojan://, vmess://, or ss:// links.",
        ).pack(anchor="w")
        ttk.Label(
            body,
            text="Each link on its own line. Duplicates are skipped automatically.",
            style="Caption.TLabel",
        ).pack(anchor="w", pady=(2, 10))

        text_wrap = ttk.Frame(body)
        text_wrap.pack(fill=tk.BOTH, expand=True)
        text_wrap.rowconfigure(0, weight=1)
        text_wrap.columnconfigure(0, weight=1)

        text = tk.Text(
            text_wrap, wrap=tk.WORD, font=self._fonts["mono"],
            relief=tk.FLAT, borderwidth=0, padx=10, pady=8,
            background=p["card_alt"], foreground=p["text"],
            insertbackground=p["text"], selectbackground=p["accent"],
            selectforeground=p["accent_fg"],
        )
        text.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(text_wrap, orient="vertical", command=text.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=vsb.set)

        footer = ttk.Frame(body)
        footer.pack(fill=tk.X, pady=(12, 0))

        detected = tk.StringVar(value="0 links detected")
        ttk.Label(footer, textvariable=detected, style="Caption.TLabel").pack(side=tk.LEFT)

        def update_count(_event=None) -> None:
            count = count_candidates(text.get("1.0", tk.END))
            detected.set(f"{count} link{'s' if count != 1 else ''} detected")

        def submit() -> None:
            raw = text.get("1.0", tk.END)
            added, dupes, errors = self.add_profiles_from_text(raw)
            dialog.destroy()
            summary = f"Added {added} · Skipped {dupes} duplicate{'s' if dupes != 1 else ''}"
            if errors:
                summary += f" · {len(errors)} failed"
            self._show_toast(summary, duration_ms=3200)

        text.bind("<KeyRelease>", update_count)
        ttk.Button(footer, text="Cancel", style="Toolbar.TButton",
                   command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(footer, text=f"{ICON_ADD}  Add", style="Accent.TButton",
                   command=submit).pack(side=tk.RIGHT, padx=(0, 8))

    def import_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Import profiles",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        raw = Path(path).read_text(encoding="utf-8", errors="ignore")
        added, dupes, errors = self.add_profiles_from_text(raw)
        summary = f"Added {added} · Skipped {dupes} duplicate{'s' if dupes != 1 else ''}"
        if errors:
            summary += f" · {len(errors)} failed"
        self._show_toast(summary, duration_ms=3200)

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
        dialog.resizable(False, False)
        self._prepare_dialog(dialog)

        body = ttk.Frame(dialog, padding=(20, 18))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="New name").pack(anchor="w")
        value = tk.StringVar(value=profile.name)
        entry = ttk.Entry(body, textvariable=value, width=46)
        entry.pack(fill=tk.X, pady=(6, 14))
        entry.focus_set()
        entry.icursor(tk.END)

        def save() -> None:
            name = value.get().strip()
            if name:
                profile.name = name
                self.store.save_profiles(self.profiles)
                self.refresh_all()
                self._show_toast(f"Renamed to “{name}”")
            dialog.destroy()

        button_row = ttk.Frame(body)
        button_row.pack(fill=tk.X)
        ttk.Button(button_row, text="Cancel", style="Toolbar.TButton",
                   command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(button_row, text="Save", style="Accent.TButton",
                   command=save).pack(side=tk.RIGHT, padx=(0, 8))
        dialog.bind("<Return>", lambda _e: save())
        dialog.bind("<Escape>", lambda _e: dialog.destroy())

    def delete_selected_profiles(self) -> None:
        selected = self.selected_profiles()
        if not selected:
            return
        if not messagebox.askyesno(
            "Delete profiles",
            f"Delete {len(selected)} selected profile(s)?",
        ):
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
        path = filedialog.asksaveasfilename(
            title="Export profiles",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt")],
        )
        if not path:
            return
        Path(path).write_text(text, encoding="utf-8")
        self._show_toast(f"Exported {len(selected)} profile{'s' if len(selected) != 1 else ''}")

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
        doomed = [
            profile for profile in self.profiles
            if self.ping_results.get(profile.id, PingResult()).millis is None
        ]
        if not doomed:
            return
        if not messagebox.askyesno(
            "Remove no ping",
            f"Remove {len(doomed)} profile(s) without a successful ping?",
        ):
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

    # ── Queue / loop ────────────────────────────────────────────────────────

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
                    self.egress_var.set(
                        f"{text} {country or ''}".strip() if ok else text
                    )
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
        tag = "stderr" if stream == "stderr" else "stdout"
        self.log_text.insert(tk.END, text, (tag,))
        if not self._autoscroll_paused:
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
        if self.controller.is_running and not messagebox.askyesno(
            "Quit Cloak", "Disconnect and quit?"
        ):
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
