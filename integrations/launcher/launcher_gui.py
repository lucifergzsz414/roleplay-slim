"""roleplay-slim 启动器 — 面向普通用户的一键接入窗口。

跟 integrations/pet-installer/ 下那三个"装器"是两种东西：
  - 装器：跑一次 → 改某个桌宠的配置 → 退出。
  - 启动器（这个）：一直开着 → 代理在后台跑 → 窗口里实时显示"这次省了多少"。

之所以要单独做一个，是因为面向普通用户时，"看得见省了多少"本身就是产品的
主要价值展示，而不是装完就看不见的后台服务。

设计上的一个关键取舍：**这个启动器不需要用户的 API key**。代理会把聊天软件
自己带的 Authorization 头原样透传给上游（见 roleplay_slim/proxy/server.py 的
_build_upstream_headers：调用方带了真 token 就用调用方的），所以用户的 key
还是填在酒馆/桌宠里，启动器从头到尾看不到、也不存。对一个公开分发的小工具
来说，这一点比省事更重要。

界面上的取舍：那个"省了多少"的条形图是整屏的主角。百分比数字是记不住的，
一条肉眼可见变短的条子才是。颜色和控件样式统一来自 theme.py，跟 app.ico 同源。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.parse import urlsplit

from theme import (
    ACCENT,
    ACCENT_DARK,
    ACCENT_SOFT,
    ACCENT_STRONG,
    BANNER,
    BANNER_DIM,
    BANNER_PANEL,
    BANNER_SUCCESS,
    BANNER_TEXT,
    BG,
    BORDER,
    BORDER_SOFT,
    CARD,
    DANGER,
    INPUT,
    PANEL,
    SUCCESS,
    SUCCESS_SOFT,
    TEXT,
    TEXT_DIM,
    TEXT_FAINT,
    WARN,
    Card,
    PillButton,
    SavingsBar,
    apply_ttk_theme,
    font,
    mono,
)


# ---------------------------------------------------------------------------
# 路径解析：冻结成 exe 之后，资源在 _MEIPASS，工作目录在 exe 旁边
# ---------------------------------------------------------------------------
def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


if _is_frozen():
    _base = Path(sys.executable).parent
    _bundle_dir = Path(getattr(sys, "_MEIPASS", _base))
else:
    _base = Path(__file__).resolve().parent
    _bundle_dir = _base

_PROXY_EXE_NAME = "roleplay-slim-proxy.exe"
DEFAULT_PORT = 8795  # Dedicated loopback port for the generic launcher.

UPSTREAM_PRESETS: dict[str, dict[str, str]] = {
    "DeepSeek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "",
    },
    "阿里云百炼": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.6-plus",
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "model": "",
    },
    "SiliconFlow 硅基流动": {
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "",
    },
    "自定义…": {
        "base_url": "",
        "model": "",
    },
}

PLATFORM_UNIVERSAL = "通用模式（任何能填 API 地址的软件）"
PLATFORMS = [PLATFORM_UNIVERSAL]


def _calculate_window_size(
    requested_width: int,
    requested_height: int,
    screen_width: int,
    screen_height: int,
) -> tuple[int, int]:
    """Fit the launcher inside the logical desktop at any DPI scale."""
    return (
        min(requested_width, int(screen_width * 0.92)),
        min(requested_height, int(screen_height * 0.92)),
    )


def _use_stacked_layout(available_width: int) -> bool:
    """Stack cards when the real viewport cannot fit both columns."""
    return available_width < 1040

CONFIG_TOML = """# roleplay-slim 启动器自动生成，不需要手动改
[proxy]
upstream_base_url = {upstream}
{upstream_model_line}upstream_api_key_env = "ROLEPLAY_SLIM_UNUSED_KEY"
host = "127.0.0.1"
port = {port}

[compressor]
keep_recent_turns = 6
enable_whitespace_normalize = true
enable_dedupe_verbatim_tail = true
enable_history_window = true
enable_strip_stage_directions = false
history_window_mode = "trim"

[stats]
persist = true
db_path = {db_path}
"""


def _work_dir() -> Path:
    """配置和统计数据库放在 exe 旁边的 data/ 里——用户能直接看到、能整个删掉，
    不往注册表或 AppData 里藏东西。"""
    d = _base / "roleplay-slim-data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_proxy_exe() -> Path | None:
    for candidate in (_base / _PROXY_EXE_NAME, _base / "dist" / _PROXY_EXE_NAME):
        if candidate.is_file():
            return candidate
    return None


def _stop_spawned_process(
    process,
    *,
    runner=subprocess.run,
    platform: str = sys.platform,
) -> None:
    """Stop the exact proxy process launched by this window.

    A PyInstaller one-file executable uses a parent/child bootloader pair on
    Windows. Terminating only the Popen handle can leave the other process
    serving the port, so Windows must stop the owned process tree.
    """
    if process.poll() is not None:
        return

    if platform in {"nt", "win32"}:
        creationflags = (
            subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        try:
            result = runner(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=creationflags,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None and result.returncode == 0:
            try:
                process.wait(timeout=5)
            except Exception:
                pass
            else:
                return

    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
            process.wait(timeout=5)
        except Exception:
            pass


def _fmt(n: int) -> str:
    return f"{n:,}"


def _validate_upstream_url(value: str) -> str:
    """Return a safe normalized HTTP(S) base URL for the generated TOML."""
    value = value.strip().rstrip("/")
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        parts.port  # force validation of malformed and out-of-range ports
    except ValueError as e:
        raise ValueError("请输入有效的 http/https 地址和端口。") from e
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or not hostname
        or not hostname.strip(".")
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or "\\" in value
        or '"' in value
        or any(ch.isspace() for ch in value)
    ):
        raise ValueError("请输入不含账号、查询参数或换行的 http/https 地址。")
    return value


def _render_config(
    *, upstream: str, model: str, port: int, db_path: Path
) -> str:
    """Render launcher TOML without allowing user text to alter its structure."""
    upstream = _validate_upstream_url(upstream)
    model = model.strip()
    model_line = (
        f"upstream_model = {json.dumps(model, ensure_ascii=False)}\n" if model else ""
    )
    return CONFIG_TOML.format(
        upstream=json.dumps(upstream, ensure_ascii=False),
        upstream_model_line=model_line,
        port=port,
        db_path=json.dumps(str(db_path), ensure_ascii=False),
    )


class LauncherApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("roleplay-slim · 长对话整理器")
        self.root.configure(bg=BG)

        self.style = apply_ttk_theme(self.root)

        self.proc: subprocess.Popen | None = None
        self.port = DEFAULT_PORT
        self._log_queue: list[tuple[str, str]] = []
        self._after_id: str | None = None
        self._stats_after_id: str | None = None
        self._last_before = 0
        self._misses = 0
        self._icon_img: tk.PhotoImage | None = None

        self._set_window_icon()
        self._build_ui()
        self._center()
        self._enable_light_titlebar()
        self._log("准备就绪。完成左侧连接设置后，点击「开始整理」。", "info")
        # Keep watching the port for the window's whole life, not only after
        # we started the proxy ourselves. Otherwise a GUI restarted while a
        # proxy is still running shows "未启动" while the port is in fact
        # occupied — and the next click on 启动 collides with it.
        self._schedule_stats()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- 杂 ---
    def _set_window_icon(self) -> None:
        """.ico works for the taskbar; the in-window header uses the PNG
        because Tk's PhotoImage can't scale and can't read .ico."""
        ico_applied = False
        for name in ("app.ico", "assets/app.ico"):
            p = _bundle_dir / name
            if p.is_file():
                try:
                    self.root.iconbitmap(default=str(p))
                    ico_applied = True
                    break
                except tk.TclError:
                    pass
        for name in ("app_header.png", "assets/app_header.png"):
            p = _bundle_dir / name
            if p.is_file():
                try:
                    self._icon_img = tk.PhotoImage(file=str(p))
                    if not ico_applied:
                        self.root.iconphoto(True, self._icon_img)
                    break
                except tk.TclError:
                    pass

    def _center(self) -> None:
        """Size the window to what the content actually needs, then centre it.

        Hard-coding a geometry meant step 3 got silently clipped off the
        bottom the moment the content grew — and the required height also
        varies with the user's DPI scaling, so a fixed number was wrong on
        some machines and right on others."""
        self.root.update_idletasks()
        requested_width = max(
            self.root.winfo_reqwidth(), getattr(self, "_preferred_width", 920)
        )
        requested_height = self.root.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = _calculate_window_size(
            requested_width, requested_height, sw, sh
        )
        self.root.minsize(min(860, w), min(620, h))
        self.root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

    def _enable_light_titlebar(self) -> None:
        """Keep the Windows title bar consistent with the light application."""
        try:
            import ctypes

            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(0)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, older builds
                res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
                )
                if res == 0:
                    break
        except Exception:
            pass

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self) -> None:
        self._build_header()
        self._preferred_width = min(
            1112, int(self.root.winfo_screenwidth() * 0.92)
        )
        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill=tk.BOTH, expand=True)

        visible_height = max(
            430, min(680, int(self.root.winfo_screenheight() * 0.72))
        )
        self.content_canvas = tk.Canvas(
            shell, bg=BG, borderwidth=0, highlightthickness=0,
            height=visible_height,
        )
        self.content_scroll = ttk.Scrollbar(
            shell, command=self.content_canvas.yview,
            style="RS.Vertical.TScrollbar",
        )
        self.content_canvas.configure(yscrollcommand=self.content_scroll.set)
        self.content_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.content_scroll.pack(
            side=tk.RIGHT, fill=tk.Y, padx=(0, 8), pady=(0, 24)
        )

        workspace = tk.Frame(self.content_canvas, bg=BG)
        self._workspace_window = self.content_canvas.create_window(
            (0, 0), window=workspace, anchor=tk.NW
        )
        workspace.bind("<Configure>", self._update_scroll_region)
        self.content_canvas.bind("<Configure>", self._resize_workspace)
        self.root.bind("<MouseWheel>", self._scroll_workspace)

        workspace.grid_columnconfigure(0, weight=1)
        self._workspace = workspace
        self._stacked_layout: bool | None = None

        self.setup_card = Card(workspace, padding=22)
        self._build_step1(self.setup_card.body)
        self._divider(self.setup_card.body, pady=18)
        self._build_step2(self.setup_card.body)

        self.activity_card = Card(workspace, padding=22)
        self._build_step3(self.activity_card.body)
        self._divider(self.activity_card.body, pady=16)
        self._build_log(self.activity_card.body)
        self._apply_workspace_layout(self._preferred_width)

    def _update_scroll_region(self, _evt=None) -> None:
        bbox = self.content_canvas.bbox("all")
        if bbox is not None:
            self.content_canvas.configure(scrollregion=bbox)
            content_height = bbox[3] - bbox[1]
            if content_height > self.content_canvas.winfo_height() + 1:
                if not self.content_scroll.winfo_manager():
                    self.content_scroll.pack(
                        side=tk.RIGHT, fill=tk.Y,
                        padx=(0, 8), pady=(0, 24),
                    )
            else:
                self.content_canvas.yview_moveto(0)
                self.content_scroll.pack_forget()

    def _resize_workspace(self, event) -> None:
        self.content_canvas.itemconfigure(self._workspace_window, width=event.width)
        self._apply_workspace_layout(event.width)
        self.root.after_idle(self._update_scroll_region)

    def _apply_workspace_layout(self, available_width: int) -> None:
        stacked = _use_stacked_layout(available_width)
        if stacked == self._stacked_layout:
            return
        self._stacked_layout = stacked
        self.setup_card.grid_forget()
        self.activity_card.grid_forget()

        if stacked:
            self._workspace.grid_columnconfigure(0, weight=1, uniform="")
            self._workspace.grid_columnconfigure(1, weight=0, uniform="")
            self.setup_card.grid(
                row=0, column=0, sticky="nsew", padx=28, pady=(0, 8)
            )
            self.activity_card.grid(
                row=1, column=0, sticky="nsew", padx=28, pady=(8, 24)
            )
        else:
            self._workspace.grid_columnconfigure(0, weight=5, uniform="workspace")
            self._workspace.grid_columnconfigure(1, weight=6, uniform="workspace")
            self.setup_card.grid(
                row=0, column=0, sticky="nsew", padx=(28, 8), pady=(0, 24)
            )
            self.activity_card.grid(
                row=0, column=1, sticky="nsew", padx=(8, 28), pady=(0, 24)
            )

        if available_width < 900:
            self.header_context_label.pack_forget()
        elif not self.header_context_label.winfo_manager():
            self.header_context_label.pack(side=tk.RIGHT, padx=(0, 12))

    def _scroll_workspace(self, event) -> None:
        if self.content_canvas.bbox("all") is None:
            return
        self.content_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _build_header(self) -> None:
        head = tk.Frame(self.root, bg=BANNER)
        head.pack(fill=tk.X, padx=28, pady=(22, 18), ipady=4)

        if self._icon_img is not None:
            tk.Label(head, image=self._icon_img, bg=BANNER).pack(
                side=tk.LEFT, padx=(0, 14)
            )
        else:
            mark = tk.Canvas(
                head, width=44, height=44, bg=BANNER,
                highlightthickness=0, bd=0,
            )
            mark.create_rectangle(1, 1, 43, 43, fill=ACCENT_STRONG, outline="")
            mark.create_line(11, 13, 32, 13, fill="#FFFFFF", width=3)
            mark.create_line(11, 22, 28, 22, fill="#FFFFFF", width=3)
            mark.create_line(11, 31, 23, 31, fill="#FFFFFF", width=3)
            mark.pack(side=tk.LEFT, padx=(0, 14))

        titles = tk.Frame(head, bg=BANNER)
        titles.pack(side=tk.LEFT, anchor=tk.W)
        tk.Label(
            titles, text="roleplay-slim",
            bg=BANNER, fg=BANNER_TEXT, font=font(17, bold=True),
        ).pack(anchor=tk.W)
        tk.Label(
            titles,
            text="长对话整理器",
            bg=BANNER, fg=BANNER_DIM, font=font(9),
        ).pack(anchor=tk.W, pady=(2, 0))

        meta = tk.Frame(head, bg=BANNER)
        meta.pack(side=tk.RIGHT, anchor=tk.E)
        tk.Label(
            meta, text="本地运行", bg=BANNER_PANEL, fg=BANNER_SUCCESS,
            font=font(8, bold=True), padx=11, pady=6,
        ).pack(side=tk.RIGHT)
        self.header_context_label = tk.Label(
            meta, text="API Key 始终由聊天软件管理",
            bg=BANNER, fg=BANNER_DIM, font=font(8),
        )
        self.header_context_label.pack(side=tk.RIGHT, padx=(0, 12))

    def _section_title(self, parent: tk.Frame, text: str, hint: str) -> None:
        row = tk.Frame(parent, bg=CARD)
        row.pack(anchor=tk.W, fill=tk.X)
        tk.Label(
            row, text=text, bg=CARD, fg=TEXT, font=font(11, bold=True)
        ).pack(anchor=tk.W)
        tk.Label(
            row, text=hint, bg=CARD, fg=TEXT_FAINT, font=font(8)
        ).pack(anchor=tk.W, pady=(3, 0))

    def _divider(self, parent: tk.Frame, pady: int = 14) -> None:
        tk.Frame(parent, bg=BORDER_SOFT, height=1).pack(
            fill=tk.X, pady=(pady, pady)
        )

    def _build_step1(self, parent: tk.Frame) -> None:
        b = parent
        self._section_title(b, "AI 服务", "选择实际处理对话的模型服务")

        row = tk.Frame(b, bg=CARD)
        row.pack(fill=tk.X, pady=(14, 0))

        tk.Label(
            row, text="服务商", bg=CARD, fg=TEXT_DIM, font=font(8, bold=True),
        ).pack(anchor=tk.W, pady=(0, 5))

        self.upstream_name = tk.StringVar(value="DeepSeek")
        combo = ttk.Combobox(
            row, textvariable=self.upstream_name, state="readonly",
            values=list(UPSTREAM_PRESETS.keys()), width=18, style="RS.TCombobox",
            font=font(9),
        )
        combo.pack(fill=tk.X)
        combo.bind("<<ComboboxSelected>>", self._on_upstream_change)

        self.upstream_url = tk.StringVar(
            value=UPSTREAM_PRESETS["DeepSeek"]["base_url"]
        )
        self.upstream_entry = tk.Entry(
            row, textvariable=self.upstream_url, font=mono(9), state="readonly",
            bg=INPUT, fg=TEXT_DIM, readonlybackground=INPUT, relief=tk.FLAT,
            insertbackground=TEXT, disabledbackground=INPUT,
            disabledforeground=TEXT_FAINT, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT_STRONG,
        )
        self.upstream_entry.pack(fill=tk.X, pady=(8, 0), ipady=7)

        tk.Label(
            row, text="模型名称", bg=CARD, fg=TEXT_DIM, font=font(8, bold=True),
        ).pack(anchor=tk.W, pady=(9, 0))
        self.upstream_model = tk.StringVar(
            value=UPSTREAM_PRESETS["DeepSeek"]["model"]
        )
        tk.Entry(
            row, textvariable=self.upstream_model, font=mono(9),
            bg=INPUT, fg=TEXT, relief=tk.FLAT, insertbackground=TEXT,
            highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT_STRONG,
        ).pack(fill=tk.X, pady=(5, 0), ipady=7)

        tk.Label(
            row, text="留空时沿用聊天软件请求的模型",
            bg=CARD, fg=TEXT_FAINT, font=font(8),
        ).pack(anchor=tk.W, pady=(5, 0))

    def _build_step2(self, parent: tk.Frame) -> None:
        b = parent
        self._section_title(b, "接入应用", "把整理后的请求交给你的聊天软件")

        # 通用版只提供本地 API 地址，不修改任何特定聊天软件的配置。
        self.url_frame = tk.Frame(b, bg=CARD)
        row = tk.Frame(self.url_frame, bg=CARD)
        row.pack(fill=tk.X)
        self.url_value = tk.StringVar(value=f"http://127.0.0.1:{self.port}/v1")
        self.url_display = tk.Entry(
            row, textvariable=self.url_value, state="readonly",
            font=mono(9, bold=True), justify=tk.LEFT,
            bg=INPUT, fg=ACCENT_DARK, readonlybackground=INPUT, relief=tk.FLAT,
            insertbackground=TEXT, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT_STRONG,
        )
        self.url_display.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=7)
        self.copy_btn = PillButton(
            row, "复制地址", command=self._copy_url, width=92, height=38,
            fill=ACCENT_SOFT, hover=BORDER, fg=ACCENT_DARK,
            font_=font(9, bold=True),
        )
        self.copy_btn.pack(side=tk.RIGHT, padx=(10, 0))
        tk.Label(
            self.url_frame, text="粘贴到聊天软件的「API 地址」栏",
            bg=CARD, fg=TEXT_DIM, font=font(8),
        ).pack(anchor=tk.W, pady=(8, 0))
        self.url_frame.pack(fill=tk.X, pady=(14, 0))

        self.security_note = tk.Frame(b, bg=SUCCESS_SOFT)
        tk.Label(
            self.security_note, text="隐私保护", bg=SUCCESS_SOFT, fg=SUCCESS,
            font=font(8, bold=True),
        ).pack(anchor=tk.W, padx=12, pady=(9, 2))
        tk.Label(
            self.security_note,
            text="启动器不会读取或保存 API Key",
            bg=SUCCESS_SOFT, fg=TEXT_DIM, font=font(8),
        ).pack(anchor=tk.W, padx=12, pady=(0, 9))
        self.security_note.pack(fill=tk.X, pady=(14, 0))

    def _build_step3(self, parent: tk.Frame) -> None:
        b = parent
        self._section_title(b, "运行状态", "启动后保持这个窗口打开即可")

        row = tk.Frame(b, bg=CARD)
        row.pack(fill=tk.X, pady=(16, 0))

        self.toggle_btn = PillButton(
            row, "开始整理", command=self._toggle, width=148, height=44,
        )
        self.toggle_btn.pack(side=tk.LEFT)

        status_box = tk.Frame(row, bg=CARD)
        status_box.pack(side=tk.LEFT, padx=(14, 0))
        self.dot = tk.Canvas(status_box, width=10, height=10, bg=CARD,
                             highlightthickness=0, bd=0)
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=TEXT_FAINT, outline="")
        self.dot.pack(side=tk.LEFT, pady=(2, 0))
        self.status_var = tk.StringVar(value="未启动")
        tk.Label(
            status_box, textvariable=self.status_var, bg=CARD, fg=TEXT_DIM,
            font=font(9),
        ).pack(side=tk.LEFT, padx=(7, 0))

        # —— 主角：省了多少 ——
        hero = tk.Frame(b, bg=PANEL)
        hero.pack(fill=tk.X, pady=(18, 0), ipady=16)
        hero_inner = tk.Frame(hero, bg=PANEL)
        hero_inner.pack(fill=tk.X, padx=16)

        self.big_var = tk.StringVar(value="—")
        self.big_label = tk.Label(
            hero_inner, textvariable=self.big_var, bg=PANEL, fg=TEXT_FAINT,
            font=mono(25, bold=True),
        )
        self.big_label.pack(anchor=tk.W)

        self.big_sub = tk.StringVar(value="启动后，这里会显示每次对话少发了多少内容")
        tk.Label(
            hero_inner, textvariable=self.big_sub, bg=PANEL, fg=TEXT_DIM, font=font(9),
        ).pack(anchor=tk.W, pady=(5, 0))

        self.bar = SavingsBar(hero_inner, width=390, height=58, bg=PANEL)
        self.bar.pack(anchor=tk.W, pady=(14, 0))

        self.total_var = tk.StringVar(value="")
        tk.Label(
            b, textvariable=self.total_var, bg=CARD, fg=TEXT_FAINT, font=font(8),
        ).pack(anchor=tk.W, pady=(10, 0))

    def _build_log(self, parent: tk.Frame) -> None:
        wrap = parent

        title_row = tk.Frame(wrap, bg=CARD)
        title_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            title_row, text="运行记录", bg=CARD, fg=TEXT_DIM,
            font=font(8, bold=True),
        ).pack(side=tk.LEFT)
        tk.Label(
            title_row, text="只显示本次启动的信息", bg=CARD, fg=TEXT_FAINT,
            font=font(8),
        ).pack(side=tk.RIGHT)

        log_body = tk.Frame(wrap, bg=CARD)
        log_body.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_body, height=5, wrap=tk.WORD, font=mono(8), state=tk.DISABLED,
            bg=PANEL, fg=TEXT_DIM, relief=tk.FLAT, borderwidth=0,
            insertbackground=TEXT, highlightthickness=1,
            highlightbackground=BORDER_SOFT, padx=10, pady=8,
        )
        scroll = ttk.Scrollbar(log_body, command=self.log_text.yview,
                               style="RS.Vertical.TScrollbar")
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text.tag_configure("ok", foreground=SUCCESS)
        self.log_text.tag_configure("warn", foreground=WARN)
        self.log_text.tag_configure("error", foreground=DANGER)
        self.log_text.tag_configure("info", foreground=TEXT_FAINT)

    # ------------------------------------------------------------- 事件 ---
    def _on_upstream_change(self, _evt=None) -> None:
        name = self.upstream_name.get()
        preset = UPSTREAM_PRESETS[name]
        if name == "自定义…":
            self.upstream_entry.configure(
                state=tk.NORMAL, fg=TEXT, bg=INPUT, readonlybackground=INPUT,
            )
            self.upstream_url.set("")
        else:
            self.upstream_entry.configure(
                state="readonly", fg=TEXT_DIM, readonlybackground=BG,
            )
            self.upstream_url.set(preset["base_url"])
        self.upstream_model.set(preset["model"])

    def _copy_url(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.url_value.get())
        self.copy_btn.configure_text("已复制")
        self.root.after(1400, lambda: self.copy_btn.configure_text("复制"))
        self._log("地址已复制到剪贴板", "ok")

    # ------------------------------------------------------------- 代理 ---
    def _toggle(self) -> None:
        if self.proc is None:
            self._start_proxy()
        else:
            self._stop_proxy()

    def _start_proxy(self) -> None:
        try:
            upstream = _validate_upstream_url(self.upstream_url.get())
        except ValueError as e:
            messagebox.showwarning("地址不正确", str(e))
            return

        work = _work_dir()
        cfg = work / "config.toml"
        cfg.write_text(
            _render_config(
                upstream=upstream,
                model=self.upstream_model.get(),
                port=self.port,
                db_path=work / "stats.db",
            ),
            encoding="utf-8",
        )

        exe = _find_proxy_exe()
        if exe is not None:
            cmd = [str(exe), "--config", str(cfg)]
        else:
            # 开发模式（没打包）时退回用当前解释器跑
            cmd = [sys.executable, "-m", "roleplay_slim.proxy", "--config", str(cfg)]
            self._log("未找到 roleplay-slim-proxy.exe，改用开发模式启动", "warn")

        creation = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(work), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=creation,
            )
        except Exception as e:
            messagebox.showerror("启动失败", f"没能启动代理：\n{e}")
            self.proc = None
            return

        self.status_var.set("正在启动…")
        self._set_dot(WARN)
        self.toggle_btn.set_enabled(False)
        threading.Thread(target=self._wait_ready, daemon=True).start()

    def _wait_ready(self) -> None:
        url = f"http://127.0.0.1:{self.port}/healthz"
        for _ in range(40):
            if self.proc is None or self.proc.poll() is not None:
                self.root.after(0, self._on_start_failed, "代理进程意外退出")
                return
            try:
                with urllib.request.urlopen(url, timeout=1) as r:
                    if r.status == 200:
                        self.root.after(0, self._on_started)
                        return
            except Exception:
                time.sleep(0.5)
        self.root.after(0, self._on_start_failed, "启动超时（可能被安全软件拦了）")

    def _on_started(self) -> None:
        self.status_var.set(f"运行中 · 127.0.0.1:{self.port}")
        self._set_dot(SUCCESS)
        self.toggle_btn.configure_text("停止整理")
        self.toggle_btn.configure_fill(TEXT_DIM, TEXT)
        self.toggle_btn.set_enabled(True)
        self._log("代理已启动，现在可以正常聊天了", "ok")
        self.big_sub.set("正常聊天就行，数字会自己动")

    def _on_start_failed(self, why: str) -> None:
        self.status_var.set("未启动")
        self._set_dot(TEXT_FAINT)
        self.toggle_btn.configure_text("开始整理")
        self.toggle_btn.configure_fill(ACCENT_STRONG, ACCENT)
        self.toggle_btn.set_enabled(True)
        self._log(f"启动失败：{why}", "error")
        self._stop_proxy(quiet=True)

    def _set_dot(self, color: str) -> None:
        self.dot.itemconfigure(self.dot_id, fill=color)

    def _stop_proxy(self, quiet: bool = False) -> None:
        # Polling deliberately keeps running after a stop — the port might
        # still be served by an instance this window didn't start, and the
        # window should keep telling the truth about that.
        if self.proc is not None:
            _stop_spawned_process(self.proc)
            self.proc = None
        self.status_var.set("未启动")
        self._set_dot(TEXT_FAINT)
        self.toggle_btn.configure_text("开始整理")
        self.toggle_btn.configure_fill(ACCENT_STRONG, ACCENT)
        self.toggle_btn.set_enabled(True)
        if not quiet:
            self._log("已停止", "warn")

    # ------------------------------------------------------------- 统计 ---
    def _schedule_stats(self) -> None:
        self._stats_after_id = self.root.after(2000, self._poll_stats)

    def _poll_stats(self) -> None:
        threading.Thread(target=self._fetch_stats, daemon=True).start()
        self._schedule_stats()

    def _fetch_stats(self) -> None:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/stats?window=1", timeout=2
            ) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception:
            self.root.after(0, self._mark_unreachable)
            return
        self.root.after(0, self._mark_reachable)
        self.root.after(0, self._render_stats, data)

    def _mark_reachable(self) -> None:
        """Something is serving our port. If it isn't the process this
        window started, say so and disable 启动 — clicking it would just
        spawn a second proxy that immediately fails to bind."""
        self._misses = 0
        if self.proc is not None:
            return
        if self.status_var.get() != "未启动":
            return  # a start is already in flight; _wait_ready owns the text
        self.status_var.set(f"运行中 · 127.0.0.1:{self.port}（由别的窗口启动）")
        self._set_dot(SUCCESS)
        self.toggle_btn.configure_text("已在运行")
        self.toggle_btn.set_enabled(False)

    def _mark_unreachable(self) -> None:
        self._misses = getattr(self, "_misses", 0) + 1
        # Two consecutive misses before flipping the light off — a single
        # failed poll during a restart shouldn't make the UI flicker.
        if self._misses < 2:
            return
        if self.proc is not None:
            return
        if self.status_var.get().startswith("运行中"):
            self.status_var.set("未启动")
            self._set_dot(TEXT_FAINT)
            self.toggle_btn.configure_text("开始整理")
            self.toggle_btn.configure_fill(ACCENT_STRONG, ACCENT)
            self.toggle_btn.set_enabled(True)
            self.big_sub.set("启动后，这里会显示每次对话少发了多少内容")
            self.bar.set_empty()

    def _render_stats(self, data: dict) -> None:
        recent = data.get("recent") or {}
        before = recent.get("tokens_before_total", 0)
        after = recent.get("tokens_after_total", 0)

        if before:
            pct = (before - after) / before * 100
            self.big_var.set(f"{_fmt(before)}  →  {_fmt(after)}")
            self.big_label.configure(fg=TEXT if pct > 0 else WARN)
            self.big_sub.set(f"最近这次请求，少发了 {pct:.0f}% 的内容")
            self.bar.set_ratio(
                after / before if before else 1.0,
                f"原本要发 {_fmt(before)} token",
                f"整理后 {_fmt(after)} token",
            )
            if before != self._last_before:
                self._last_before = before
                self.bar.flash()

        total_saved = data.get("tokens_saved_total", 0)
        count = data.get("request_count", 0)
        if count:
            self.total_var.set(
                f"累计 {count} 次对话  ·  一共少发 {_fmt(total_saved)} token"
            )

    # --------------------------------------------------------------- 杂 ---
    def _log(self, msg: str, tag: str = "") -> None:
        self._log_queue.append((msg, tag))
        if self._after_id is None:
            self._after_id = self.root.after(50, self._drain_log)

    def _drain_log(self) -> None:
        self._after_id = None
        self.log_text.configure(state=tk.NORMAL)
        while self._log_queue:
            msg, tag = self._log_queue.pop(0)
            if self.log_text.get("1.0", tk.END).strip():
                self.log_text.insert(tk.END, "\n")
            self.log_text.insert(tk.END, msg, tag or ())
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _on_close(self) -> None:
        self._stop_proxy(quiet=True)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    LauncherApp().run()


if __name__ == "__main__":
    main()
