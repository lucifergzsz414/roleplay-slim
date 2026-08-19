"""GUI installer for roleplay-slim + BandoriPet Voice Append (Electron pet).

Double-click to install. No terminal, no Python, no pip — just pick the
win-unpacked directory and click.
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

sys.path.insert(0, str(_bundle_dir / "installer_bandori"))

from patch_bandori import (  # noqa: E402
    BACKUP_SUFFIX,
    CONFIG_TOML,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_CHAT_URL,
    LAUNCH_PET_BAT,
    LAUNCH_PROXY_BAT,
    PROXY_PORT,
    find_ai_config,
    find_index_html,
    patch_text_file,
)

_PROXY_EXE_NAME = "roleplay-slim-proxy.exe"


def _find_proxy_exe() -> Path | None:
    for candidate in (_base / _PROXY_EXE_NAME, _base / "dist" / _PROXY_EXE_NAME):
        if candidate.is_file():
            return candidate
    return None


def _auto_detect_pet_dir() -> str:
    candidates = [
        Path("E:/壁纸/BANDORI"),
        Path("E:/BANDORI"),
        Path("D:/BANDORI"),
    ]
    for base in candidates:
        try:
            if not base.is_dir():
                continue
            for child in base.rglob("win-unpacked"):
                if (child / "resources" / "app" / "index.html").is_file():
                    return str(child)
        except OSError:
            continue
    return ""


class InstallerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("BandoriPet · 上下文优化代理 安装工具")
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
            self.root, text="BandoriPet · 上下文优化代理",
            font=("Microsoft YaHei UI", 13, "bold"),
        )
        title.pack(pady=(16, 2))

        subtitle = ttk.Label(
            self.root, text="让 AI 记忆更聪明，同时节省 DeepSeek API 费用",
            font=("Microsoft YaHei UI", 9),
        )
        subtitle.pack(pady=(0, 14))

        dir_frame = ttk.LabelFrame(self.root, text="桌宠安装目录 (win-unpacked)", padding=10)
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
        self._log("roleplay-slim BandoriPet 安装工具", tag="bold")
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
        path = filedialog.askdirectory(title="选择 BandoriPet 的 win-unpacked 目录")
        if path:
            self.dir_var.set(path)

    def _start_install(self) -> None:
        if self._install_running:
            return

        pet_dir = self.dir_var.get().strip()
        if not pet_dir:
            messagebox.showwarning("缺少目录", "请先选择桌宠的 win-unpacked 目录。")
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
        log("[1/5] 查找关键文件...", tag="bold")
        try:
            index_path = find_index_html(pet_dir)
            log(f"  index.html: {index_path}", tag="ok")
        except FileNotFoundError as e:
            log(f"  {e}", tag="error")
            self.root.after(0, lambda: messagebox.showerror(
                "找不到 index.html",
                f"在以下目录找不到 resources\\app\\index.html:\n{pet_dir}\n\n"
                "请确认这是正确的 win-unpacked 目录。",
            ))
            return

        ai_config = find_ai_config(pet_dir)
        if ai_config:
            log(f"  ai_config.json: {ai_config}", tag="ok")

        log("")
        log("[2/5] 备份原文件...", tag="bold")
        backup = index_path.with_name(index_path.name + BACKUP_SUFFIX)
        if not backup.exists():
            shutil.copy2(index_path, backup)
            log(f"  index.html → 备份", tag="ok")
        else:
            log(f"  备份已存在，跳过", tag="info")

        ai_backup = None
        if ai_config:
            ai_backup = ai_config.with_name(ai_config.name + BACKUP_SUFFIX)
            if not ai_backup.exists():
                shutil.copy2(ai_config, ai_backup)
                log(f"  ai_config.json → 备份", tag="ok")

        log("")
        log("[3/5] Patch index.html (硬编码版本才有此字符串)...", tag="bold")
        proxy_url = f"http://127.0.0.1:{PROXY_PORT}/v1/chat/completions"
        index_result = patch_text_file(index_path, DEEPSEEK_CHAT_URL, proxy_url, log=log)

        ai_config_result = None
        if ai_config:
            log("")
            log("[3.5/5] Patch ai_config.json (官方原版走这条路径)...", tag="bold")
            proxy_base = f"http://127.0.0.1:{PROXY_PORT}/v1"
            ai_config_result = patch_text_file(ai_config, DEEPSEEK_BASE_URL, proxy_base, log=log)

        # BandoriPet ships in two layouts depending on whether the
        # community "long-term memory" patch is installed: patched
        # versions hardcode the URL in index.html, stock versions route
        # through ai_config.json via aiService.js. Only a real failure if
        # NEITHER matched — one not matching just means this version uses
        # the other mechanism.
        if index_result is None and ai_config_result is None:
            log(f"  index.html 和 ai_config.json 都未找到匹配的 DeepSeek URL", tag="error")
            self.root.after(0, lambda: messagebox.showerror(
                "Patch 失败",
                "index.html 和 ai_config.json 中都未找到 DeepSeek URL。\n"
                "这个版本可能既不是记忆补丁版本也不是已知的官方版本，需要人工排查。\n"
                "本次未对任何文件做改动。",
            ))
            return

        log("")
        log("[4/5] 生成代理配置...", tag="bold")
        config_dir = pet_dir / "roleplay-slim-proxy"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "config.toml").write_text(
            CONFIG_TOML.format(port=PROXY_PORT), encoding="utf-8"
        )
        log(f"  config.toml", tag="ok")

        proxy_src = _find_proxy_exe()
        if proxy_src:
            try:
                shutil.copy2(proxy_src, config_dir / _PROXY_EXE_NAME)
                log(f"  {_PROXY_EXE_NAME}", tag="ok")
            except OSError as e:
                log(f"  复制代理失败: {e}", tag="warn")
        else:
            log(f"  代理程序未找到，请手动放入: {config_dir}", tag="warn")

        log("")
        log("[5/5] 创建启动脚本...", tag="bold")
        (pet_dir / "启动代理.bat").write_text(LAUNCH_PROXY_BAT, encoding="gbk")
        log(f"  启动代理.bat", tag="ok")
        (pet_dir / "BandoriPet.bat").write_text(
            LAUNCH_PET_BAT.replace("{port}", str(PROXY_PORT)), encoding="gbk"
        )
        log(f"  BandoriPet.bat", tag="ok")

        log("")
        log("=" * 50, tag="info")
        log("  安装完成！", tag="ok")
        log(f"  以后双击「BandoriPet.bat」启动即可", tag="bold")
        log("=" * 50, tag="info")

        self.root.after(0, lambda: messagebox.showinfo(
            "安装完成",
            "安装成功！\n\n以后双击「BandoriPet.bat」启动桌宠即可。\n"
            "代理会随桌宠自动启停，无需手动管理。",
        ))


def main() -> None:
    app = InstallerApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
