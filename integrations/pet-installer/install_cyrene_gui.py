"""GUI installer for roleplay-slim + Cyrene-Agent (Electron desktop pet).

Double-click to install. No terminal, no Python, no pip — just pick the
Cyrene install directory and click.
"""

from __future__ import annotations

import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


if _is_frozen():
    _base = Path(sys.executable).parent
    _bundle_dir = Path(getattr(sys, "_MEIPASS", _base))
else:
    _base = Path(__file__).resolve().parent
    _bundle_dir = _base

sys.path.insert(0, str(_bundle_dir / "installer_cyrene"))

from patch_cyrene import (  # noqa: E402
    BACKUP_SUFFIX,
    CONFIG_TOML,
    LAUNCH_PET_BAT,
    LAUNCH_PROXY_BAT,
    PROXY_PORT,
    find_cyrene_exe,
    find_model_settings_path,
    patch_model_settings,
)

_PROXY_EXE_NAME = "roleplay-slim-proxy.exe"


def _find_proxy_exe() -> Path | None:
    for candidate in (_base / _PROXY_EXE_NAME, _base / "dist" / _PROXY_EXE_NAME):
        if candidate.is_file():
            return candidate
    return None


def _auto_detect_pet_dir() -> str:
    candidates = [
        Path("D:/Cyrene1"),
        Path("D:/Cyrene"),
        Path("C:/Program Files/Cyrene"),
    ]
    for base in candidates:
        try:
            if (base / "Cyrene.exe").is_file():
                return str(base)
        except OSError:
            continue
    return ""


class InstallerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Cyrene-Agent · 上下文优化代理 安装工具")
        self.root.geometry("640x460")
        self.root.minsize(560, 400)

        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = self.root.winfo_reqwidth()
        h = self.root.winfo_reqheight()
        self.root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        self._install_running = False
        self._log_queue: list[tuple[str, str]] = []
        self._after_id: str | None = None

        self._build_ui()
        self._initial_log()

    def _build_ui(self) -> None:
        title = ttk.Label(
            self.root, text="Cyrene-Agent · 上下文优化代理",
            font=("Microsoft YaHei UI", 13, "bold"),
        )
        title.pack(pady=(16, 2))

        subtitle = ttk.Label(
            self.root, text="让 AI 记忆更聪明，同时节省 DeepSeek API 费用",
            font=("Microsoft YaHei UI", 9),
        )
        subtitle.pack(pady=(0, 14))

        dir_frame = ttk.LabelFrame(self.root, text="Cyrene 安装目录", padding=10)
        dir_frame.pack(fill=tk.X, padx=16, pady=(0, 10))

        self.dir_var = tk.StringVar(value=_auto_detect_pet_dir())
        dir_entry = ttk.Entry(dir_frame, textvariable=self.dir_var, font=("Consolas", 9))
        dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        browse_btn = ttk.Button(dir_frame, text="浏览...", command=self._browse_dir)
        browse_btn.pack(side=tk.RIGHT)

        log_frame = ttk.LabelFrame(self.root, text="安装进度", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 10))

        self.log_text = tk.Text(
            log_frame, height=12, wrap=tk.WORD, font=("Consolas", 9),
            state=tk.DISABLED, background="#1e1e1e", foreground="#d4d4d4",
            insertbackground="#d4d4d4", relief=tk.FLAT, borderwidth=0,
        )
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text.tag_configure("ok", foreground="#6a9955")
        self.log_text.tag_configure("warn", foreground="#ce9178")
        self.log_text.tag_configure("error", foreground="#f44747")
        self.log_text.tag_configure("info", foreground="#569cd6")
        self.log_text.tag_configure("bold", foreground="#dcdcaa", font=("Consolas", 9, "bold"))

        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=16, pady=(0, 14))

        self.install_btn = ttk.Button(btn_frame, text="▶  开始安装", command=self._start_install)
        self.install_btn.pack(side=tk.RIGHT, padx=(10, 0))

        close_btn = ttk.Button(btn_frame, text="关闭", command=self.root.destroy)
        close_btn.pack(side=tk.RIGHT)

    def _initial_log(self) -> None:
        self._log("roleplay-slim Cyrene-Agent 安装工具", tag="bold")
        self._log(f"代理端口: 127.0.0.1:{PROXY_PORT}")
        proxy = _find_proxy_exe()
        if proxy:
            self._log(f"代理程序: {proxy.name} 已找到", tag="ok")
        else:
            self._log("代理程序: 未找到 (请确保 roleplay-slim-proxy.exe 与本程序在同一目录)", tag="warn")
        self._log("")

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
            if tag:
                self.log_text.insert(tk.END, msg, tag)
            else:
                self.log_text.insert(tk.END, msg)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _browse_dir(self) -> None:
        path = filedialog.askdirectory(title="选择 Cyrene 的安装目录")
        if path:
            self.dir_var.set(path)

    def _start_install(self) -> None:
        if self._install_running:
            return

        pet_dir = self.dir_var.get().strip()
        if not pet_dir:
            messagebox.showwarning("缺少目录", "请先选择 Cyrene 的安装目录。")
            return

        pet_path = Path(pet_dir)
        if not pet_path.is_dir():
            messagebox.showwarning("目录不存在", f"目录不存在:\n{pet_dir}")
            return

        self._install_running = True
        self.install_btn.configure(state=tk.DISABLED, text="⏳ 安装中...")

        thread = threading.Thread(target=self._run_install, args=(pet_path,), daemon=True)
        thread.start()

    def _run_install(self, pet_dir: Path) -> None:
        try:
            self._install(pet_dir)
        except Exception as exc:
            self._log("", tag="")
            self._log(f"安装失败: {exc}", tag="error")
        finally:
            self.root.after(0, self._install_done)

    def _install_done(self) -> None:
        self._install_running = False
        self.install_btn.configure(state=tk.NORMAL, text="▶  重新安装")

    def _install(self, pet_dir: Path) -> None:
        log = self._log

        log("=" * 50, tag="info")
        log("[1/4] 查找关键文件...", tag="bold")
        exe = find_cyrene_exe(pet_dir)
        if exe:
            log(f"  Cyrene.exe: {exe}", tag="ok")
        else:
            log("  未找到 Cyrene.exe —— 启动脚本仍会生成，但请确认目录正确", tag="warn")

        try:
            settings_path = find_model_settings_path()
            log(f"  model-settings.json 位置: {settings_path}", tag="ok")
        except FileNotFoundError as e:
            log(f"  {e}", tag="error")
            self.root.after(0, lambda: messagebox.showerror(
                "找不到用户数据目录",
                f"{e}\n\n请先把 Cyrene 完整启动运行一次（走完首次引导）再安装。",
            ))
            return

        log("")
        log("[2/4] 备份配置文件...", tag="bold")
        if settings_path.is_file():
            backup = settings_path.with_name(settings_path.name + BACKUP_SUFFIX)
            if not backup.exists():
                shutil.copy2(settings_path, backup)
                log("  model-settings.json → 备份", tag="ok")
            else:
                log("  备份已存在，跳过", tag="info")
        else:
            log("  model-settings.json 尚不存在，跳过备份（安装时会创建默认配置）", tag="info")

        log("")
        log("[3/4] 修改模型配置...", tag="bold")
        patch_model_settings(settings_path, PROXY_PORT, log=log)

        log("")
        log("[4/4] 生成代理配置与启动脚本...", tag="bold")
        config_dir = pet_dir / "roleplay-slim-proxy"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "config.toml").write_text(
            CONFIG_TOML.format(port=PROXY_PORT), encoding="utf-8"
        )
        log("  config.toml", tag="ok")

        proxy_src = _find_proxy_exe()
        if proxy_src:
            try:
                shutil.copy2(proxy_src, config_dir / _PROXY_EXE_NAME)
                log(f"  {_PROXY_EXE_NAME}", tag="ok")
            except OSError as e:
                log(f"  复制代理失败: {e}", tag="warn")
        else:
            log(f"  代理程序未找到，请手动放入: {config_dir}", tag="warn")

        (pet_dir / "启动代理.bat").write_text(LAUNCH_PROXY_BAT, encoding="gbk")
        log("  启动代理.bat", tag="ok")
        (pet_dir / "Cyrene启动.bat").write_text(
            LAUNCH_PET_BAT.replace("{port}", str(PROXY_PORT)), encoding="gbk"
        )
        log("  Cyrene启动.bat", tag="ok")

        log("")
        log("=" * 50, tag="info")
        log("  安装完成！", tag="ok")
        log("  以后双击「Cyrene启动.bat」启动即可", tag="bold")
        log("=" * 50, tag="info")

        self.root.after(0, lambda: messagebox.showinfo(
            "安装完成",
            "安装成功！\n\n以后双击「Cyrene启动.bat」启动 Cyrene 即可。\n"
            "代理会随 Cyrene 自动启停，无需手动管理。\n\n"
            "如果 model-settings.json 是本次新建的，记得去 Cyrene 的设置页面里\n"
            "把 API Key 填上（安装器不会读取或存储你的真实 key）。",
        ))


def main() -> None:
    app = InstallerApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
