"""Shared visual system for the Windows installer family."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

BG = "#F4F7FB"
CARD = "#FFFFFF"
BANNER = "#0F172A"
BANNER_PANEL = "#1E293B"
BANNER_TEXT = "#FFFFFF"
BANNER_DIM = "#CBD5E1"
BORDER = "#D9E2EE"
INPUT = "#F7F9FC"
PANEL = "#F8FAFC"
TEXT = "#0F172A"
TEXT_DIM = "#475569"
TEXT_FAINT = "#64748B"
ACCENT = "#2563EB"
ACCENT_HOVER = "#1D4ED8"
SUCCESS = "#047857"
DANGER = "#B42318"
DANGER_HOVER = "#912018"

FONT = "Microsoft YaHei UI"
MONO = "Consolas"


def fit_window_size(
    preferred_width: int,
    preferred_height: int,
    screen_width: int,
    screen_height: int,
) -> tuple[int, int]:
    """Fit a preferred logical size within the current DPI-scaled desktop."""
    return (
        min(preferred_width, int(screen_width * 0.92)),
        min(preferred_height, int(screen_height * 0.92)),
    )


def apply_theme(root: tk.Tk) -> ttk.Style:
    root.configure(bg=BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure("Installer.TLabel", background=CARD, foreground=TEXT, font=(FONT, 9))
    style.configure(
        "Installer.Secondary.TLabel",
        background=CARD,
        foreground=TEXT_DIM,
        font=(FONT, 8),
    )
    style.configure(
        "Installer.TEntry",
        fieldbackground=INPUT,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=(9, 7),
    )
    style.configure(
        "Installer.TCombobox",
        fieldbackground=INPUT,
        foreground=TEXT,
        bordercolor=BORDER,
        arrowcolor=TEXT_DIM,
        padding=(8, 6),
    )
    style.map(
        "Installer.TCombobox",
        fieldbackground=[("readonly", INPUT)],
        foreground=[("readonly", TEXT)],
    )
    style.configure(
        "Installer.TButton",
        background=PANEL,
        foreground=TEXT,
        bordercolor=BORDER,
        padding=(14, 8),
        font=(FONT, 9),
    )
    style.map("Installer.TButton", background=[("active", "#E8EEF7")])
    style.configure(
        "Installer.Primary.TButton",
        background=ACCENT,
        foreground="#FFFFFF",
        bordercolor=ACCENT,
        padding=(18, 9),
        font=(FONT, 9, "bold"),
    )
    style.map(
        "Installer.Primary.TButton",
        background=[("active", ACCENT_HOVER), ("disabled", "#94A3B8")],
        foreground=[("disabled", "#E2E8F0")],
    )
    style.configure(
        "Installer.Danger.TButton",
        background=DANGER,
        foreground="#FFFFFF",
        bordercolor=DANGER,
        padding=(18, 9),
        font=(FONT, 9, "bold"),
    )
    style.map(
        "Installer.Danger.TButton",
        background=[("active", DANGER_HOVER), ("disabled", "#C7A6A2")],
        foreground=[("disabled", "#F8E8E7")],
    )
    return style


def load_brand_icon(root: tk.Tk, bundle_dir: Path) -> tk.PhotoImage | None:
    """Apply the Windows ICO and return the PNG used inside the header."""
    asset_roots = (
        bundle_dir,
        bundle_dir / "assets",
        bundle_dir.parent / "launcher" / "assets",
    )
    ico_applied = False
    for root_dir in asset_roots:
        path = root_dir / "app.ico"
        if not path.is_file():
            continue
        try:
            root.iconbitmap(default=str(path))
            ico_applied = True
            break
        except tk.TclError:
            pass

    for root_dir in asset_roots:
        path = root_dir / "app_header.png"
        if not path.is_file():
            continue
        try:
            image = tk.PhotoImage(file=str(path))
            if not ico_applied:
                root.iconphoto(True, image)
            return image
        except tk.TclError:
            pass
    return None


def build_header(
    root: tk.Tk,
    image: tk.PhotoImage | None,
    *,
    title: str,
    subtitle: str,
    badge: str,
) -> None:
    header = tk.Frame(root, bg=BANNER)
    header.pack(fill=tk.X, padx=24, pady=(20, 16), ipady=4)

    if image is not None:
        tk.Label(header, image=image, bg=BANNER).pack(side=tk.LEFT, padx=(0, 14))

    copy = tk.Frame(header, bg=BANNER)
    copy.pack(side=tk.LEFT)
    tk.Label(
        copy, text=title, bg=BANNER, fg=BANNER_TEXT,
        font=(FONT, 15, "bold"),
    ).pack(anchor=tk.W)
    tk.Label(
        copy, text=subtitle, bg=BANNER, fg=BANNER_DIM,
        font=(FONT, 8),
    ).pack(anchor=tk.W, pady=(2, 0))

    tk.Label(
        header, text=badge, bg=BANNER_PANEL, fg="#A7F3D0",
        font=(FONT, 8, "bold"), padx=11, pady=6,
    ).pack(side=tk.RIGHT)


def create_card(parent: tk.Misc, title: str, hint: str = "") -> tk.Frame:
    card = tk.Frame(
        parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER,
    )
    card.pack(fill=tk.X, padx=24, pady=(0, 12))
    body = tk.Frame(card, bg=CARD)
    body.pack(fill=tk.BOTH, expand=True, padx=18, pady=16)
    tk.Label(
        body, text=title, bg=CARD, fg=TEXT, font=(FONT, 10, "bold"),
    ).pack(anchor=tk.W)
    if hint:
        tk.Label(
            body, text=hint, bg=CARD, fg=TEXT_FAINT, font=(FONT, 8),
        ).pack(anchor=tk.W, pady=(3, 12))
    return body


class ScrollableBody(tk.Frame):
    """Vertical content area that only shows its scrollbar when necessary."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, bg=BG)
        self.canvas = tk.Canvas(
            self, bg=BG, borderwidth=0, highlightthickness=0,
        )
        self.scrollbar = ttk.Scrollbar(
            self, command=self.canvas.yview, orient=tk.VERTICAL,
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.body = tk.Frame(self.canvas, bg=BG)
        self._window = self.canvas.create_window(
            (0, 0), window=self.body, anchor=tk.NW
        )
        self.body.bind("<Configure>", self._sync_scroll_region)
        self.canvas.bind("<Configure>", self._resize_body)

    def _resize_body(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self.after_idle(self._sync_scroll_region)

    def _sync_scroll_region(self, _event=None) -> None:
        bbox = self.canvas.bbox("all")
        if bbox is None:
            return
        self.canvas.configure(scrollregion=bbox)
        content_height = bbox[3] - bbox[1]
        if content_height > self.canvas.winfo_height() + 1:
            if not self.scrollbar.winfo_manager():
                self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 8))
        else:
            self.canvas.yview_moveto(0)
            self.scrollbar.pack_forget()

    def on_mousewheel(self, event) -> str:
        if isinstance(event.widget, (tk.Text, tk.Listbox, ttk.Combobox)):
            return "break"
        if self.canvas.bbox("all") is not None:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"


def center_window(
    root: tk.Tk,
    *,
    preferred_width: int,
    preferred_height: int,
    minimum_width: int,
    minimum_height: int,
) -> None:
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    width, height = fit_window_size(preferred_width, preferred_height, sw, sh)
    root.minsize(min(minimum_width, width), min(minimum_height, height))
    root.geometry(
        f"{width}x{height}+{max(0, (sw - width) // 2)}+"
        f"{max(0, (sh - height) // 2)}"
    )
