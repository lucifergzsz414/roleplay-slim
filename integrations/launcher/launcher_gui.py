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
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit

from theme import (
    ACCENT,
    ACCENT_DARK,
    ACCENT_STRONG,
    BANNER,
    BG,
    BORDER,
    CARD,
    DANGER,
    SUCCESS,
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

# 三个已适配桌宠的 patch 模块：延迟导入（用户没选到那个平台就不需要），
# 但路径要提前挂上——PyInstaller 只认构建期能静态分析到的东西，运行期
# sys.path.insert 对它不可见，所以 build.py 里必须同时给 --paths。
for _sub in ("installer", "installer_bandori", "installer_cyrene"):
    _candidate = _bundle_dir / _sub
    if _candidate.is_dir():
        sys.path.insert(0, str(_candidate))

_PROXY_EXE_NAME = "roleplay-slim-proxy.exe"
DEFAULT_PORT = 8795  # 8791=若叶睦 8792=邦多利 8793=Cyrene，这里另起一个避免打架

UPSTREAM_PRESETS: dict[str, str] = {
    "DeepSeek": "https://api.deepseek.com/v1",
    "OpenAI": "https://api.openai.com/v1",
    "SiliconFlow 硅基流动": "https://api.siliconflow.cn/v1",
    "自定义…": "",
}

PLATFORM_UNIVERSAL = "通用模式（任何能填 API 地址的软件）"
PLATFORM_MUTSUMI = "若叶睦桌宠"
PLATFORM_BANDORI = "邦多利桌宠 BandoriPet"
PLATFORM_CYRENE = "Cyrene-Agent"
PLATFORMS = [PLATFORM_UNIVERSAL, PLATFORM_MUTSUMI, PLATFORM_BANDORI, PLATFORM_CYRENE]

CONFIG_TOML = """# roleplay-slim 启动器自动生成，不需要手动改
[proxy]
upstream_base_url = "{upstream}"
upstream_api_key_env = "ROLEPLAY_SLIM_UNUSED_KEY"
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
db_path = "{db_path}"
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


class LauncherApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("聊天记录整理器 · roleplay-slim")
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
        self._enable_dark_titlebar()
        self._log("准备好了，选好上面的两项就可以点「启动」。", "info")
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
        for name in ("app.ico", "assets/app.ico"):
            p = _bundle_dir / name
            if p.is_file():
                try:
                    self.root.iconbitmap(default=str(p))
                    break
                except tk.TclError:
                    pass
        for name in ("app_header.png", "assets/app_header.png"):
            p = _bundle_dir / name
            if p.is_file():
                try:
                    self._icon_img = tk.PhotoImage(file=str(p))
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
        w = max(self.root.winfo_reqwidth(), 700)
        h = self.root.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        h = min(h, int(sh * 0.92))  # never taller than the screen
        self.root.minsize(min(660, w), min(620, h))
        self.root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

    def _enable_dark_titlebar(self) -> None:
        """Windows 10 1809+ draws a light title bar by default, which looks
        broken on top of a dark window. This is a one-call DWM flag; it's
        best-effort on purpose — older Windows just keeps the light bar."""
        try:
            import ctypes

            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(1)
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
        setup = tk.Frame(self.root, bg=BG)
        setup.pack(fill=tk.X, padx=20, pady=(0, 10))
        setup.grid_columnconfigure(0, weight=1, uniform="setup")
        setup.grid_columnconfigure(1, weight=1, uniform="setup")
        self._build_step1(setup)
        self._build_step2(setup)
        self._build_step3()
        self._build_log()
        self._on_platform_change()

    def _build_header(self) -> None:
        head = tk.Frame(self.root, bg=BANNER)
        head.pack(fill=tk.X, padx=20, pady=(18, 14), ipady=12)

        if self._icon_img is not None:
            tk.Label(head, image=self._icon_img, bg=BANNER).pack(side=tk.LEFT, padx=(14, 12))

        titles = tk.Frame(head, bg=BANNER)
        titles.pack(side=tk.LEFT, anchor=tk.W)
        tk.Label(
            titles, text="聊得越久，角色越不像她？",
            bg=BANNER, fg=TEXT, font=font(15, bold=True),
        ).pack(anchor=tk.W)
        tk.Label(
            titles,
            text="保护人设和最近的对话，只整理越来越长的旧聊天记录。",
            bg=BANNER, fg=TEXT_DIM, font=font(9),
        ).pack(anchor=tk.W, pady=(3, 0))

        badge = tk.Label(
            head, text="本地运行  ·  Key 不落盘", bg=BORDER, fg=SUCCESS,
            font=font(8, bold=True), padx=12, pady=6,
        )
        badge.pack(side=tk.RIGHT, padx=(10, 14))

    def _step_title(self, parent: tk.Frame, num: str, text: str) -> None:
        row = tk.Frame(parent, bg=CARD)
        row.pack(anchor=tk.W, fill=tk.X)
        tk.Label(
            row, text=num, bg=ACCENT_STRONG, fg="#FFFFFF",
            font=font(8, bold=True), width=2, height=1,
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(row, text=text, bg=CARD, fg=TEXT, font=font(10, bold=True)).pack(side=tk.LEFT)

    def _build_step1(self, parent: tk.Frame) -> None:
        card = Card(parent, padding=14)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        b = card.body
        self._step_title(b, "1", "你的 AI 服务商")

        row = tk.Frame(b, bg=CARD)
        row.pack(fill=tk.X, pady=(10, 0))

        self.upstream_name = tk.StringVar(value="DeepSeek")
        combo = ttk.Combobox(
            row, textvariable=self.upstream_name, state="readonly",
            values=list(UPSTREAM_PRESETS.keys()), width=18, style="RS.TCombobox",
            font=font(9),
        )
        combo.pack(fill=tk.X)
        combo.bind("<<ComboboxSelected>>", self._on_upstream_change)

        self.upstream_url = tk.StringVar(value=UPSTREAM_PRESETS["DeepSeek"])
        self.upstream_entry = tk.Entry(
            row, textvariable=self.upstream_url, font=mono(9), state="readonly",
            bg=BG, fg=TEXT_DIM, readonlybackground=BG, relief=tk.FLAT,
            insertbackground=TEXT, disabledbackground=BG, disabledforeground=TEXT_FAINT,
        )
        self.upstream_entry.pack(fill=tk.X, pady=(8, 0), ipady=5)

        tk.Label(
            b, text="✓  无需在这里填写 API Key",
            bg=CARD, fg=SUCCESS, font=font(8),
        ).pack(anchor=tk.W, pady=(9, 0))

    def _build_step2(self, parent: tk.Frame) -> None:
        card = Card(parent, padding=14)
        card.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        b = card.body
        self._step_title(b, "2", "你用什么聊天")

        self.platform = tk.StringVar(value=PLATFORM_UNIVERSAL)
        combo = ttk.Combobox(
            b, textvariable=self.platform, state="readonly", values=PLATFORMS,
            style="RS.TCombobox", font=font(9),
        )
        combo.pack(fill=tk.X, pady=(10, 0))
        combo.bind("<<ComboboxSelected>>", self._on_platform_change)

        # —— 通用模式：一个大字地址 + 复制 ——
        self.url_frame = tk.Frame(b, bg=CARD)
        row = tk.Frame(self.url_frame, bg=CARD)
        row.pack(fill=tk.X)
        self.url_value = tk.StringVar(value=f"http://127.0.0.1:{self.port}/v1")
        self.url_display = tk.Entry(
            row, textvariable=self.url_value, state="readonly",
            font=mono(10, bold=True), justify=tk.CENTER,
            bg=BG, fg=ACCENT, readonlybackground=BG, relief=tk.FLAT,
            insertbackground=TEXT,
        )
        self.url_display.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
        self.copy_btn = PillButton(
            row, "复制", command=self._copy_url, width=74, height=36,
            fill=BORDER, hover=ACCENT_DARK, font_=font(9, bold=True),
        )
        self.copy_btn.pack(side=tk.RIGHT, padx=(10, 0))
        tk.Label(
            self.url_frame, text="复制到聊天软件的「API 地址」栏",
            bg=CARD, fg=TEXT_DIM, font=font(8),
        ).pack(anchor=tk.W, pady=(8, 0))

        # —— 已适配应用：一键改配置 ——
        self.patch_frame = tk.Frame(b, bg=CARD)
        self.patch_btn = PillButton(
            self.patch_frame, "一键改好它的配置", command=self._one_click_patch,
            width=170, height=36, font_=font(9, bold=True),
        )
        self.patch_btn.pack(side=tk.LEFT)
        tk.Label(
            self.patch_frame, text="会先备份原配置，可随时还原",
            bg=CARD, fg=TEXT_FAINT, font=font(8),
        ).pack(side=tk.LEFT, padx=(10, 0))

    def _build_step3(self) -> None:
        card = Card(self.root, padding=16)
        card.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))
        b = card.body
        self._step_title(b, "3", "开着它，然后像平常一样聊天")

        row = tk.Frame(b, bg=CARD)
        row.pack(fill=tk.X, pady=(12, 0))

        self.toggle_btn = PillButton(
            row, "▶  启动", command=self._toggle, width=132, height=40,
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
        hero = tk.Frame(b, bg=CARD)
        hero.pack(fill=tk.X, pady=(14, 0))

        self.big_var = tk.StringVar(value="—")
        self.big_label = tk.Label(
            hero, textvariable=self.big_var, bg=CARD, fg=TEXT_FAINT,
            font=mono(24, bold=True),
        )
        self.big_label.pack()

        self.big_sub = tk.StringVar(value="启动后，这里会显示每次对话少发了多少内容")
        tk.Label(
            hero, textvariable=self.big_sub, bg=CARD, fg=TEXT_DIM, font=font(9),
        ).pack(pady=(6, 0))

        self.bar = SavingsBar(hero, width=560, height=58, bg=CARD)
        self.bar.pack(pady=(14, 0))

        self.total_var = tk.StringVar(value="")
        tk.Label(
            b, textvariable=self.total_var, bg=CARD, fg=TEXT_FAINT, font=font(8),
        ).pack(pady=(14, 0))

    def _build_log(self) -> None:
        card = Card(self.root, padding=10)
        card.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 16))
        wrap = card.body

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
            log_body, height=3, wrap=tk.WORD, font=mono(8), state=tk.DISABLED,
            bg=CARD, fg=TEXT_DIM, relief=tk.FLAT, borderwidth=0,
            insertbackground=TEXT, highlightthickness=0,
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
        if name == "自定义…":
            self.upstream_entry.configure(
                state=tk.NORMAL, fg=TEXT, bg=BG, readonlybackground=BG,
            )
            self.upstream_url.set("")
        else:
            self.upstream_entry.configure(
                state="readonly", fg=TEXT_DIM, readonlybackground=BG,
            )
            self.upstream_url.set(UPSTREAM_PRESETS[name])

    def _on_platform_change(self, _evt=None) -> None:
        if self.platform.get() == PLATFORM_UNIVERSAL:
            self.patch_frame.pack_forget()
            self.url_frame.pack(fill=tk.X, pady=(12, 0))
        else:
            self.url_frame.pack_forget()
            self.patch_frame.pack(fill=tk.X, pady=(12, 0))

    def _copy_url(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.url_value.get())
        self.copy_btn.configure_text("已复制 ✓")
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
            CONFIG_TOML.format(
                upstream=upstream, port=self.port,
                db_path=str(work / "stats.db").replace("\\", "\\\\"),
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
        self.toggle_btn.configure_text("■  停止")
        self.toggle_btn.configure_fill(BORDER, ACCENT_DARK)
        self.toggle_btn.set_enabled(True)
        self._log("代理已启动，现在可以正常聊天了", "ok")
        self.big_sub.set("正常聊天就行，数字会自己动")

    def _on_start_failed(self, why: str) -> None:
        self.status_var.set("未启动")
        self._set_dot(TEXT_FAINT)
        self.toggle_btn.configure_text("▶  启动")
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
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        self.status_var.set("未启动")
        self._set_dot(TEXT_FAINT)
        self.toggle_btn.configure_text("▶  启动")
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
            self.toggle_btn.configure_text("▶  启动")
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

    # --------------------------------------------------------- 一键接入 ---
    def _one_click_patch(self) -> None:
        platform = self.platform.get()
        pet_dir = filedialog.askdirectory(title=f"选择 {platform} 的安装目录")
        if not pet_dir:
            return
        self.patch_btn.set_enabled(False)
        threading.Thread(
            target=self._run_patch, args=(platform, Path(pet_dir)), daemon=True
        ).start()

    def _run_patch(self, platform: str, pet_dir: Path) -> None:
        def log(msg: str) -> None:
            self._log(str(msg))

        try:
            if platform == PLATFORM_CYRENE:
                import patch_cyrene

                settings = patch_cyrene.find_model_settings_path()
                patch_cyrene.patch_model_settings(settings, self.port, log=log)
            elif platform == PLATFORM_BANDORI:
                import patch_bandori

                index_path = patch_bandori.find_index_html(pet_dir)
                patch_bandori.patch_text_file(
                    index_path, patch_bandori.DEEPSEEK_CHAT_URL,
                    f"http://127.0.0.1:{self.port}/v1/chat/completions", log=log,
                )
                ai_config = patch_bandori.find_ai_config(pet_dir)
                if ai_config:
                    patch_bandori.patch_text_file(
                        ai_config, patch_bandori.DEEPSEEK_BASE_URL,
                        f"http://127.0.0.1:{self.port}/v1", log=log,
                    )
            elif platform == PLATFORM_MUTSUMI:
                import install as mutsumi_install

                mutsumi_install.patch_registry_url(self.port, log=log)
                mutsumi_install.patch_file(mutsumi_install.find_level0(pet_dir), log=log)
                mutsumi_install.patch_file(
                    mutsumi_install.find_metadata(pet_dir), check_prefix=False, log=log)
            else:
                return
        except Exception as e:
            error_message = f"改配置失败：{e}"
            self.root.after(0, lambda: self._patch_done(error_message, ok=False))
            return
        self.root.after(
            0, lambda: self._patch_done(f"{platform} 配置已改好，重启它就能生效", ok=True)
        )

    def _patch_done(self, msg: str, ok: bool) -> None:
        self._log(msg, "ok" if ok else "error")
        self.patch_btn.set_enabled(True)

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
