"""Small Tkinter theming kit for the launcher.

Tkinter's stock widgets look like 1998, which matters here: this window is
the thing ordinary users see first, and it's what shows up on screen in the
demo video. Rather than pull in a dependency, this module keeps the few
pieces that actually need to look good — a rounded pill button, a card, and
a rounded progress bar — as thin Canvas-based widgets, and configures ttk
for the rest.

Everything is flat, borderless and driven by one palette below, so
restyling is a one-place change.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ---------------------------------------------------------------------------
# Palette — violet accent, matching assets/app.ico
# ---------------------------------------------------------------------------
BG = "#12141A"
CARD = "#1A1D26"
BANNER = "#161923"
BORDER = "#262A36"
BORDER_SOFT = "#1F2330"

TEXT = "#E9EAEE"
TEXT_DIM = "#8B90A0"
TEXT_FAINT = "#5C6273"

ACCENT = "#A78BFA"
ACCENT_STRONG = "#7C5CF6"
ACCENT_DARK = "#5B3FD6"

SUCCESS = "#4ADE80"
WARN = "#FBBF24"
DANGER = "#F87171"

FONT = "Microsoft YaHei UI"
MONO = "Consolas"


def font(size: int = 10, bold: bool = False) -> tuple:
    return (FONT, size, "bold") if bold else (FONT, size)


def mono(size: int = 10, bold: bool = False) -> tuple:
    return (MONO, size, "bold") if bold else (MONO, size)


def round_rect_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list[float]:
    """Point list for a rounded rectangle drawn as a smoothed polygon.

    Canvas has no rounded-rect primitive; the smoothed-polygon trick is the
    standard workaround and antialiases better than stitching arcs+lines.
    """
    r = min(r, abs(x2 - x1) / 2, abs(y2 - y1) / 2)
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class Card(tk.Frame):
    """A flat panel with a hairline border. Put children in `card.body`.

    Rounded corners are skipped on purpose: faking them behind child
    widgets means either a Canvas host (which breaks normal geometry
    management) or per-corner image masks, and at this size a crisp 1px
    border reads just as modern.
    """

    def __init__(self, master, padding: int = 16, **kw):
        super().__init__(master, bg=BORDER_SOFT, **kw)  # this frame *is* the border
        holder = tk.Frame(self, bg=CARD)
        holder.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        self.body = tk.Frame(holder, bg=CARD)
        self.body.pack(fill=tk.BOTH, expand=True, padx=padding, pady=padding)


class PillButton(tk.Canvas):
    """Rounded, flat, hover-aware button. The stock ttk.Button can't be
    given a fill colour on Windows without switching themes and still
    renders a grey chrome border, which looks wrong on a dark panel."""

    def __init__(
        self, master, text: str, command=None, *,
        width: int = 150, height: int = 40,
        fill: str = ACCENT_STRONG, hover: str = ACCENT,
        fg: str = "#FFFFFF", font_: tuple | None = None, radius: int | None = None,
        bg: str = CARD,
    ):
        super().__init__(
            master, width=width, height=height, bg=bg,
            highlightthickness=2, highlightbackground=bg,
            highlightcolor=ACCENT, bd=0, cursor="hand2", takefocus=True,
        )
        self._command = command
        self._fill = fill
        self._hover = hover
        self._enabled = True
        self._radius = radius if radius is not None else height // 2

        self._shape = self.create_polygon(
            round_rect_points(1, 1, width - 1, height - 1, self._radius),
            smooth=True, fill=fill, outline="",
        )
        self._label = self.create_text(
            width / 2, height / 2, text=text, fill=fg,
            font=font_ or font(10, bold=True),
        )

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<KeyRelease-Return>", self._on_click)
        self.bind("<KeyRelease-space>", self._on_click)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)

    # -- state ------------------------------------------------------------
    def configure_text(self, text: str) -> None:
        self.itemconfigure(self._label, text=text)

    def configure_fill(self, fill: str, hover: str | None = None) -> None:
        self._fill = fill
        self._hover = hover or fill
        self.itemconfigure(self._shape, fill=fill)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.itemconfigure(self._shape, fill=self._fill if enabled else BORDER)
        self.itemconfigure(self._label, fill="#FFFFFF" if enabled else TEXT_FAINT)
        self.configure(cursor="hand2" if enabled else "arrow")

    # -- events -----------------------------------------------------------
    def _on_enter(self, _e=None) -> None:
        if self._enabled:
            self.itemconfigure(self._shape, fill=self._hover)

    def _on_leave(self, _e=None) -> None:
        if self._enabled:
            self.itemconfigure(self._shape, fill=self._fill)

    def _on_click(self, _e=None) -> str:
        if self._enabled and self._command:
            self._command()
        return "break"

    def _on_focus_in(self, _e=None) -> None:
        if self._enabled:
            self.configure(highlightbackground=ACCENT, highlightcolor=ACCENT)

    def _on_focus_out(self, _e=None) -> None:
        self.configure(highlightbackground=self.cget("bg"))


class SavingsBar(tk.Canvas):
    """Two stacked rounded bars: the full original length, and the shorter
    compressed one. This is the screen's main visual — a percentage in text
    is forgettable, a bar that visibly shrinks is not."""

    def __init__(self, master, width: int = 420, height: int = 54, bg: str = CARD, **kw):
        super().__init__(
            master, width=width, height=height, bg=bg,
            highlightthickness=0, bd=0, **kw,
        )
        # NOT self._w: tkinter stores the widget's Tcl pathname there, and
        # overwriting it makes every later Canvas call fail with
        # `invalid command name "560"`. Bit me once already.
        self._bar_w = width

        # Vertical rhythm, computed rather than hand-tuned: bar, its label,
        # next bar, its label. The first version squeezed the labels into
        # the 12px between bars and they rendered on top of the bars.
        bar_h = 13
        label_gap = 4          # bar bottom -> its label top
        label_h = 12           # an 8pt line, with a little slack
        sec_gap = 8            # label bottom -> next bar top
        r = bar_h / 2

        top_y = 2
        top_label_y = top_y + bar_h + label_gap
        bot_y = top_label_y + label_h + sec_gap
        bot_label_y = bot_y + bar_h + label_gap
        needed_h = bot_label_y + label_h + 2
        if height < needed_h:
            height = needed_h
            self.configure(height=height)

        self._track = self.create_polygon(
            round_rect_points(0, top_y, width, top_y + bar_h, r),
            smooth=True, fill=BORDER, outline="",
        )
        self._fill = self.create_polygon(
            round_rect_points(0, bot_y, width * 0.999, bot_y + bar_h, r),
            smooth=True, fill=ACCENT_STRONG, outline="",
        )
        self._top_label = self.create_text(
            0, top_label_y, anchor="nw", text="", fill=TEXT_FAINT, font=font(8),
        )
        self._bot_label = self.create_text(
            0, bot_label_y, anchor="nw", text="", fill=ACCENT, font=font(8),
        )
        self._bar_h = bar_h
        self._bot_y = bot_y
        self._r = r
        self.set_empty()

    def set_empty(self) -> None:
        """Before any request has been seen. Both bars full width but muted,
        with labels that explain what the two bars are — otherwise the
        widget is just two unexplained stripes."""
        self.coords(
            self._fill,
            *round_rect_points(0, self._bot_y, self._bar_w, self._bot_y + self._bar_h, self._r),
        )
        self.itemconfigure(self._fill, fill=BORDER)
        self.itemconfigure(self._top_label, text="原本要发的内容", fill=TEXT_FAINT)
        self.itemconfigure(
            self._bot_label, text="整理之后实际发的（还没有数据）", fill=TEXT_FAINT
        )

    def set_ratio(self, ratio: float, before_text: str = "", after_text: str = "") -> None:
        ratio = max(0.02, min(1.0, ratio))
        r = self._bar_h / 2
        self.coords(
            self._fill,
            *round_rect_points(0, self._bot_y, max(self._bar_w * ratio, r * 2 + 1),
                               self._bot_y + self._bar_h, r),
        )
        # set_ratio must own the colour too — set_empty() leaves it grey, so
        # without this the bar goes live still wearing the empty-state grey.
        self.itemconfigure(self._fill, fill=ACCENT_STRONG)
        self.itemconfigure(self._top_label, text=before_text, fill=TEXT_FAINT)
        self.itemconfigure(self._bot_label, text=after_text, fill=ACCENT)

    def flash(self) -> None:
        """Brief accent pulse when a new request lands — makes the moment
        legible on screen recording, where a number silently changing is
        easy to miss."""
        self.itemconfigure(self._fill, fill=ACCENT)
        self.after(220, lambda: self.itemconfigure(self._fill, fill=ACCENT_STRONG))


def apply_ttk_theme(root: tk.Misc) -> ttk.Style:
    """Configure the few ttk widgets still in use (combobox, scrollbar).
    'clam' is the only stock theme on Windows that honours colour options."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:  # pragma: no cover - only if clam is unavailable
        pass

    style.configure(
        "RS.TCombobox",
        fieldbackground=CARD, background=CARD, foreground=TEXT,
        arrowcolor=TEXT_DIM, bordercolor=BORDER, lightcolor=BORDER,
        darkcolor=BORDER, selectbackground=CARD, selectforeground=TEXT,
        padding=8,
    )
    style.map(
        "RS.TCombobox",
        fieldbackground=[("readonly", CARD)],
        bordercolor=[("focus", ACCENT_STRONG), ("hover", BORDER)],
        arrowcolor=[("hover", ACCENT)],
    )
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_STRONG)
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
    root.option_add("*TCombobox*Listbox.font", font(9))

    style.configure(
        "RS.Vertical.TScrollbar",
        background=BORDER, troughcolor=BG, bordercolor=BG,
        arrowcolor=TEXT_FAINT, darkcolor=BORDER, lightcolor=BORDER,
    )
    return style
