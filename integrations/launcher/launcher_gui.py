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

# ---------------------------------------------------------------------------
# 上游预设：只是 base URL，不含任何密钥
# ---------------------------------------------------------------------------
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


class LauncherApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("roleplay-slim 启动器")
        self.root.geometry("680x600")
        self.root.minsize(620, 560)

        self.proc: subprocess.Popen | None = None
        self.port = DEFAULT_PORT
        self._log_queue: list[tuple[str, str]] = []
        self._after_id: str | None = None
        self._stats_after_id: str | None = None

        self._build_ui()
        self._center()
        self._initial_log()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _center(self) -> None:
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        self.root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self) -> None:
        ttk.Label(
            self.root, text="聊得越久，角色越不像她？",
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(pady=(14, 2))
        ttk.Label(
            self.root,
            text="这个小工具会保护人设和最近的对话，只整理越来越长的旧聊天记录。",
            font=("Microsoft YaHei UI", 9),
        ).pack(pady=(0, 12))

        # —— 第一步：上游 ——
        step1 = ttk.LabelFrame(self.root, text="第 1 步 · 你的 AI 服务商", padding=10)
        step1.pack(fill=tk.X, padx=16, pady=(0, 8))

        row1 = ttk.Frame(step1)
        row1.pack(fill=tk.X)
        self.upstream_name = tk.StringVar(value="DeepSeek")
        combo = ttk.Combobox(
            row1, textvariable=self.upstream_name, state="readonly",
            values=list(UPSTREAM_PRESETS.keys()), width=22,
        )
        combo.pack(side=tk.LEFT)
        combo.bind("<<ComboboxSelected>>", self._on_upstream_change)

        self.upstream_url = tk.StringVar(value=UPSTREAM_PRESETS["DeepSeek"])
        self.upstream_entry = ttk.Entry(
            row1, textvariable=self.upstream_url, font=("Consolas", 9), state="readonly",
        )
        self.upstream_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        ttk.Label(
            step1, text="不需要填 API Key —— 你的 key 还是填在聊天软件里，这个工具看不到。",
            font=("Microsoft YaHei UI", 8), foreground="#2e7d32",
        ).pack(anchor=tk.W, pady=(6, 0))

        # —— 第二步：平台 ——
        step2 = ttk.LabelFrame(self.root, text="第 2 步 · 你用什么聊天", padding=10)
        step2.pack(fill=tk.X, padx=16, pady=(0, 8))

        self.platform = tk.StringVar(value=PLATFORM_UNIVERSAL)
        pcombo = ttk.Combobox(
            step2, textvariable=self.platform, state="readonly", values=PLATFORMS,
        )
        pcombo.pack(fill=tk.X)
        pcombo.bind("<<ComboboxSelected>>", self._on_platform_change)

        self.url_frame = ttk.Frame(step2)
        self.url_frame.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(self.url_frame, text="把这个地址填进软件的「API 地址」：",
                  font=("Microsoft YaHei UI", 9)).pack(anchor=tk.W)
        urlrow = ttk.Frame(self.url_frame)
        urlrow.pack(fill=tk.X, pady=(4, 0))
        self.url_value = tk.StringVar(value=f"http://127.0.0.1:{self.port}/v1")
        url_display = ttk.Entry(
            urlrow, textvariable=self.url_value, state="readonly",
            font=("Consolas", 11), justify=tk.CENTER,
        )
        url_display.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(urlrow, text="复制", width=8, command=self._copy_url).pack(
            side=tk.RIGHT, padx=(8, 0))

        self.patch_frame = ttk.Frame(step2)
        self.patch_btn = ttk.Button(
            self.patch_frame, text="一键改好它的配置", command=self._one_click_patch)
        self.patch_btn.pack(side=tk.LEFT)
        ttk.Label(
            self.patch_frame, text="（会先备份原配置，可随时还原）",
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT, padx=(8, 0))

        # —— 第三步：开关 + 实时数字 ——
        step3 = ttk.LabelFrame(self.root, text="第 3 步 · 开着它，然后正常聊天", padding=10)
        step3.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))

        btnrow = ttk.Frame(step3)
        btnrow.pack(fill=tk.X)
        self.toggle_btn = ttk.Button(btnrow, text="▶  启动", width=14, command=self._toggle)
        self.toggle_btn.pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="未启动")
        ttk.Label(btnrow, textvariable=self.status_var,
                  font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=(12, 0))

        self.big_var = tk.StringVar(value="—")
        ttk.Label(step3, textvariable=self.big_var,
                  font=("Consolas", 17, "bold"), foreground="#1565c0").pack(pady=(14, 2))
        self.big_sub = tk.StringVar(value="启动后，这里会显示每次对话省下多少")
        ttk.Label(step3, textvariable=self.big_sub,
                  font=("Microsoft YaHei UI", 9)).pack()

        self.total_var = tk.StringVar(value="")
        ttk.Label(step3, textvariable=self.total_var,
                  font=("Microsoft YaHei UI", 9), foreground="#555").pack(pady=(10, 0))

        self.log_text = tk.Text(
            step3, height=6, wrap=tk.WORD, font=("Consolas", 8), state=tk.DISABLED,
            background="#1e1e1e", foreground="#d4d4d4", relief=tk.FLAT, borderwidth=0,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        self.log_text.tag_configure("ok", foreground="#6a9955")
        self.log_text.tag_configure("warn", foreground="#ce9178")
        self.log_text.tag_configure("error", foreground="#f44747")

        self._on_platform_change()

    # ------------------------------------------------------------- 事件 ---
    def _on_upstream_change(self, _evt=None) -> None:
        name = self.upstream_name.get()
        if name == "自定义…":
            self.upstream_entry.configure(state=tk.NORMAL)
            self.upstream_url.set("")
        else:
            self.upstream_entry.configure(state="readonly")
            self.upstream_url.set(UPSTREAM_PRESETS[name])

    def _on_platform_change(self, _evt=None) -> None:
        if self.platform.get() == PLATFORM_UNIVERSAL:
            self.patch_frame.pack_forget()
        else:
            self.patch_frame.pack(fill=tk.X, pady=(8, 0))

    def _copy_url(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.url_value.get())
        self._log("地址已复制到剪贴板", "ok")

    # ------------------------------------------------------------- 代理 ---
    def _toggle(self) -> None:
        if self.proc is None:
            self._start_proxy()
        else:
            self._stop_proxy()

    def _start_proxy(self) -> None:
        upstream = self.upstream_url.get().strip()
        if not upstream:
            messagebox.showwarning("还差一步", "请先选择或填写你的 AI 服务商地址。")
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
        self.toggle_btn.configure(state=tk.DISABLED)
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
        self.toggle_btn.configure(state=tk.NORMAL, text="■  停止")
        self._log("代理已启动，现在可以正常聊天了", "ok")
        self.big_sub.set("正常聊天就行，下面的数字会自己动")
        self._schedule_stats()

    def _on_start_failed(self, why: str) -> None:
        self.status_var.set("未启动")
        self.toggle_btn.configure(state=tk.NORMAL, text="▶  启动")
        self._log(f"启动失败：{why}", "error")
        self._stop_proxy(quiet=True)

    def _stop_proxy(self, quiet: bool = False) -> None:
        if self._stats_after_id is not None:
            self.root.after_cancel(self._stats_after_id)
            self._stats_after_id = None
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
        self.toggle_btn.configure(text="▶  启动", state=tk.NORMAL)
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
            return
        self.root.after(0, self._render_stats, data)

    def _render_stats(self, data: dict) -> None:
        recent = data.get("recent") or {}
        before = recent.get("tokens_before_total", 0)
        after = recent.get("tokens_after_total", 0)
        if before:
            pct = (before - after) / before * 100
            self.big_var.set(f"{_fmt(before)}  →  {_fmt(after)}")
            self.big_sub.set(f"最近这次请求，少发了 {pct:.0f}% 的内容")
        total_saved = data.get("tokens_saved_total", 0)
        count = data.get("request_count", 0)
        if count:
            self.total_var.set(f"累计 {count} 次对话，一共省下 {_fmt(total_saved)} token")

    # --------------------------------------------------------- 一键接入 ---
    def _one_click_patch(self) -> None:
        platform = self.platform.get()
        pet_dir = filedialog.askdirectory(title=f"选择 {platform} 的安装目录")
        if not pet_dir:
            return
        threading.Thread(
            target=self._run_patch, args=(platform, Path(pet_dir)), daemon=True
        ).start()

    def _run_patch(self, platform: str, pet_dir: Path) -> None:
        log = lambda m: self._log(m)  # noqa: E731
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
            self._log(f"改配置失败：{e}", "error")
            return
        self._log(f"{platform} 配置已改好，重启它就能生效", "ok")

    # --------------------------------------------------------------- 杂 ---
    def _initial_log(self) -> None:
        exe = _find_proxy_exe()
        if exe:
            self._log(f"已找到代理程序：{exe.name}", "ok")
        else:
            self._log("未找到 roleplay-slim-proxy.exe（开发模式下会用 Python 直接跑）", "warn")

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
