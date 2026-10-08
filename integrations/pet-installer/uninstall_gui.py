"""GUI uninstaller for roleplay-slim + Mutsumi Desktop Pet.

Restores the desktop pet to its pre-install state: puts back the original
level0 / global-metadata.dat from their .bak backups, and removes every
file the installer added (proxy exe, config, launcher scripts). The .bak
backups themselves are never deleted — only ever restored from.
"""

from __future__ import annotations

import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


def _is_frozen() -> bool:
    """True when running inside a PyInstaller one-file bundle."""
    return getattr(sys, "frozen", False)


if _is_frozen():
    _base = Path(sys.executable).parent
    _bundle_dir = Path(getattr(sys, "_MEIPASS", _base))
else:
    _base = Path(__file__).resolve().parent
    _bundle_dir = _base

sys.path.insert(0, str(_bundle_dir / "installer"))

from install import uninstall  # noqa: E402
from installer_theme import (  # noqa: E402
    ACCENT,
    BG,
    CARD,
    PANEL,
    SUCCESS,
    TEXT,
    TEXT_DIM,
    ScrollableBody,
    apply_theme,
    build_header,
    center_window,
    create_card,
    load_brand_icon,
)
from safe_process import stop_installed_proxy  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_PROXY_PORT = 8791


def _auto_detect_pet_dir() -> str:
    """Best-effort guess of the pet installation directory — reuse the same
    heuristic as the installer so a directory picked once "just works" for
    both tools."""
    candidates = [
        Path("E:/若叶睦桌宠/若叶睦桌宠"),
        Path("E:/若叶睦桌宠"),
        Path("D:/若叶睦"),
    ]
    for c in candidates:
        try:
            if (c / "若叶睦桌宠.exe").exists():
                return str(c)
            if (c / "若叶睦桌宠_Data").is_dir():
                return str(c)
        except OSError:
            continue
    try:
        for child in Path("E:/").iterdir():
            if child.is_dir() and (child / "若叶睦桌宠_Data").is_dir():
                return str(child)
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class UninstallerApp:
    """Tkinter GUI that restores a patched pet install back to stock."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("若叶睦桌宠 · 卸载还原工具")
        apply_theme(self.root)
        self._icon_img = load_brand_icon(self.root, _bundle_dir)

        self._running = False
        self._log_queue: list[tuple[str, str]] = []
        self._after_id: str | None = None

        self._build_ui()
        self._initial_log()
        center_window(
            self.root,
            preferred_width=680,
            preferred_height=740,
            minimum_width=600,
            minimum_height=480,
        )

    def _build_ui(self) -> None:
        build_header(
            self.root,
            self._icon_img,
            title="roleplay-slim",
            subtitle="若叶睦桌宠卸载还原工具",
            badge="备份保留",
        )

        self.scroll_body = ScrollableBody(self.root)
        self.scroll_body.pack(fill=tk.BOTH, expand=True)
        self.root.bind_all("<MouseWheel>", self.scroll_body.on_mousewheel)

        dir_card = create_card(
            self.scroll_body.body,
            "还原位置",
            "选择需要恢复到原始状态的桌宠目录",
        )
        dir_frame = tk.Frame(dir_card, bg=CARD)
        dir_frame.pack(fill=tk.X)

        self.dir_var = tk.StringVar(value=_auto_detect_pet_dir())
        dir_entry = ttk.Entry(
            dir_frame,
            textvariable=self.dir_var,
            font=("Consolas", 9),
            style="Installer.TEntry",
        )
        dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        browse_btn = ttk.Button(
            dir_frame,
            text="浏览目录",
            command=self._browse_dir,
            style="Installer.TButton",
        )
        browse_btn.pack(side=tk.RIGHT)

        scope_card = create_card(
            self.scroll_body.body,
            "处理说明",
            "执行前会再次请求确认",
        )
        scope_rows = (
            ("恢复原文件", "使用现有 .bak 备份还原 level0 和 metadata"),
            ("移除代理文件", "删除代理程序、配置和启动脚本"),
            ("保留恢复备份", ".bak 文件始终保留，不会被删除"),
        )
        for index, (label, value) in enumerate(scope_rows):
            row = tk.Frame(scope_card, bg=CARD)
            row.pack(fill=tk.X, pady=(0, 7 if index < len(scope_rows) - 1 else 0))
            tk.Label(
                row,
                text=label,
                bg=CARD,
                fg=TEXT,
                font=("Microsoft YaHei UI", 9, "bold"),
                width=12,
                anchor=tk.W,
            ).pack(side=tk.LEFT)
            tk.Label(
                row,
                text=value,
                bg=CARD,
                fg=TEXT_DIM,
                font=("Microsoft YaHei UI", 9),
                anchor=tk.W,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        log_card = create_card(
            self.scroll_body.body,
            "处理日志",
            "还原结果会在这里逐项显示",
        )
        log_frame = tk.Frame(log_card, bg=CARD)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_frame,
            height=7,
            width=1,
            wrap=tk.WORD,
            font=("Consolas", 9),
            state=tk.DISABLED,
            background=PANEL,
            foreground=TEXT_DIM,
            insertbackground=TEXT,
            relief=tk.FLAT,
            borderwidth=0,
            padx=12,
            pady=10,
        )
        self.log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=self.log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text.tag_configure("ok", foreground=SUCCESS)
        self.log_text.tag_configure("warn", foreground="#B45309")
        self.log_text.tag_configure("error", foreground="#B91C1C")
        self.log_text.tag_configure("info", foreground=ACCENT)
        self.log_text.tag_configure(
            "bold", foreground=TEXT, font=("Consolas", 9, "bold")
        )

        btn_frame = tk.Frame(self.root, bg=BG)
        btn_frame.pack(fill=tk.X, padx=24, pady=(4, 18))

        self.uninstall_btn = ttk.Button(
            btn_frame,
            text="卸载并还原",
            command=self._start_uninstall,
            style="Installer.Danger.TButton",
        )
        self.uninstall_btn.pack(side=tk.RIGHT, padx=(10, 0))

        close_btn = ttk.Button(
            btn_frame,
            text="关闭",
            command=self.root.destroy,
            style="Installer.TButton",
        )
        close_btn.pack(side=tk.RIGHT)

    def _initial_log(self) -> None:
        self._log("roleplay-slim 卸载还原工具", tag="bold")
        self._log("会做的事：还原 level0/metadata → 删除代理程序、配置、启动脚本")
        self._log("不会做的事：不会删除 .bak 备份文件本身")
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
        path = filedialog.askdirectory(title="选择若叶睦桌宠的安装目录")
        if path:
            self.dir_var.set(path)

    def _start_uninstall(self) -> None:
        if self._running:
            return

        pet_dir = self.dir_var.get().strip()
        if not pet_dir:
            messagebox.showwarning("缺少目录", "请先选择桌宠的安装目录。")
            return

        pet_path = Path(pet_dir)
        if not pet_path.is_dir():
            messagebox.showwarning("目录不存在", f"目录不存在:\n{pet_dir}")
            return

        confirmed = messagebox.askyesno(
            "确认卸载",
            "这会把桌宠恢复到安装代理之前的原始状态，并删除本次安装的所有文件"
            "（代理程序、配置、启动脚本）。\n\n"
            "确定要继续吗？",
            icon="warning",
        )
        if not confirmed:
            return

        self._running = True
        self.uninstall_btn.configure(state=tk.DISABLED, text="正在处理…")

        thread = threading.Thread(
            target=self._run_uninstall, args=(pet_path,), daemon=True
        )
        thread.start()

    def _run_uninstall(self, pet_dir: Path) -> None:
        try:
            if stop_installed_proxy(pet_dir, _PROXY_PORT):
                self._log("已停止正在运行的代理进程", tag="info")
            uninstall(pet_dir, log=self._log)
        except Exception as exc:
            self._log("", tag="")
            self._log(f"卸载失败: {exc}", tag="error")
        finally:
            self.root.after(0, self._uninstall_done)

    def _uninstall_done(self) -> None:
        self._running = False
        self.uninstall_btn.configure(state=tk.NORMAL, text="卸载并还原")
        messagebox.showinfo("完成", "处理完成，请查看日志确认还原结果。")


def main() -> None:
    app = UninstallerApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
