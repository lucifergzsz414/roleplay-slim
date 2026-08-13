"""GUI uninstaller for roleplay-slim + BandoriPet Voice Append.

Restores index.html / ai_config.json from their .bak backups and removes
every file the installer added. Backups themselves are never deleted.
"""

from __future__ import annotations

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

# patch_bandori.py sits alongside this file. When frozen it lands at the
# bundle root, so _bundle_dir covers both cases without a subdirectory.
sys.path.insert(0, str(_bundle_dir))

from patch_bandori import uninstall_bandori  # noqa: E402

_PROXY_PORT = 8792


def _auto_detect_pet_dir() -> str:
    candidates = [Path("E:/壁纸/BANDORI"), Path("E:/BANDORI"), Path("D:/BANDORI")]
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


def _stop_running_proxy() -> bool:
    try:
        import subprocess

        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                f"$c=Get-NetTCPConnection -LocalPort {_PROXY_PORT} -ErrorAction Stop;"
                f"Stop-Process -Id $c.OwningProcess -Force;Write-Output $c.OwningProcess",
            ],
            capture_output=True, text=True, timeout=10,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


class UninstallerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("BandoriPet · 卸载还原工具")
        self.root.geometry("600x420")
        self.root.minsize(520, 360)

        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = self.root.winfo_reqwidth()
        h = self.root.winfo_reqheight()
        self.root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        self._running = False
        self._log_queue: list[tuple[str, str]] = []
        self._after_id: str | None = None

        self._build_ui()
        self._initial_log()

    def _build_ui(self) -> None:
        title = ttk.Label(
            self.root, text="BandoriPet · 卸载还原工具",
            font=("Microsoft YaHei UI", 13, "bold"),
        )
        title.pack(pady=(16, 2))

        subtitle = ttk.Label(
            self.root, text="把桌宠恢复到安装代理之前的原始状态",
            font=("Microsoft YaHei UI", 9),
        )
        subtitle.pack(pady=(0, 14))

        dir_frame = ttk.LabelFrame(self.root, text="桌宠目录 (win-unpacked)", padding=10)
        dir_frame.pack(fill=tk.X, padx=16, pady=(0, 10))

        self.dir_var = tk.StringVar(value=_auto_detect_pet_dir())
        dir_entry = ttk.Entry(dir_frame, textvariable=self.dir_var, font=("Consolas", 9))
        dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        browse_btn = ttk.Button(dir_frame, text="浏览...", command=self._browse_dir)
        browse_btn.pack(side=tk.RIGHT)

        log_frame = ttk.LabelFrame(self.root, text="日志", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 10))

        self.log_text = tk.Text(
            log_frame, height=10, wrap=tk.WORD, font=("Consolas", 9),
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

        self.uninstall_btn = ttk.Button(btn_frame, text="卸载 / 还原", command=self._start_uninstall)
        self.uninstall_btn.pack(side=tk.RIGHT, padx=(10, 0))

        close_btn = ttk.Button(btn_frame, text="关闭", command=self.root.destroy)
        close_btn.pack(side=tk.RIGHT)

    def _initial_log(self) -> None:
        self._log("roleplay-slim BandoriPet 卸载还原工具", tag="bold")
        self._log("会做的事：还原 index.html/ai_config.json → 删除代理程序、配置、启动脚本")
        self._log("不会做的事：不会删除 .roleplay-slim.bak 备份文件本身")
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

    def _start_uninstall(self) -> None:
        if self._running:
            return

        pet_dir = self.dir_var.get().strip()
        if not pet_dir:
            messagebox.showwarning("缺少目录", "请先选择桌宠的 win-unpacked 目录。")
            return

        pet_path = Path(pet_dir)
        if not pet_path.is_dir():
            messagebox.showwarning("目录不存在", f"目录不存在:\n{pet_dir}")
            return

        confirmed = messagebox.askyesno(
            "确认卸载",
            "这会把桌宠恢复到安装代理之前的原始状态，并删除本次安装的所有文件"
            "（代理程序、配置、启动脚本）。\n\n确定要继续吗？",
            icon="warning",
        )
        if not confirmed:
            return

        self._running = True
        self.uninstall_btn.configure(state=tk.DISABLED, text="⏳ 处理中...")

        thread = threading.Thread(target=self._run_uninstall, args=(pet_path,), daemon=True)
        thread.start()

    def _run_uninstall(self, pet_dir: Path) -> None:
        try:
            if _stop_running_proxy():
                self._log("已停止正在运行的代理进程", tag="info")
            uninstall_bandori(pet_dir, log=self._log)
        except Exception as exc:
            self._log("", tag="")
            self._log(f"卸载失败: {exc}", tag="error")
        finally:
            self.root.after(0, self._uninstall_done)

    def _uninstall_done(self) -> None:
        self._running = False
        self.uninstall_btn.configure(state=tk.NORMAL, text="卸载 / 还原")
        messagebox.showinfo("完成", "处理完成，请查看日志确认还原结果。")


def main() -> None:
    app = UninstallerApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
