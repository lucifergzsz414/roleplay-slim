"""GUI installer for roleplay-slim + Mutsumi Desktop Pet.

Double-click to install. No terminal, no Python, no pip — just pick your
pet directory and click.

When packaged with PyInstaller, this becomes a standalone ``安装器.exe``
that bundles its own Python runtime.
"""

from __future__ import annotations

import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


# ---------------------------------------------------------------------------
# Import install.py helpers (sibling directory)
# ---------------------------------------------------------------------------
def _is_frozen() -> bool:
    """True when running inside a PyInstaller one-file bundle."""
    return getattr(sys, "frozen", False)


if _is_frozen():
    # Production layout: roleplay-slim-proxy.exe sits next to 安装器.exe on disk.
    _base = Path(sys.executable).parent
    # --add-data bundled files (like install.py) extract to PyInstaller's
    # onefile temp dir at runtime, not next to the exe itself.
    _bundle_dir = Path(getattr(sys, "_MEIPASS", _base))
else:
    _base = Path(__file__).resolve().parent
    _bundle_dir = _base

sys.path.insert(0, str(_bundle_dir / "installer"))

from install import (  # noqa: E402
    LAUNCH_PET_BAT,
    LAUNCH_PROXY_BAT,
    PROVIDER_PRESETS,
    PROXY_PORT,
    find_level0,
    find_metadata,
    normalize_upstream_base_url,
    patch_file,
    patch_registry_url,
    read_api_key_from_registry,
    render_proxy_config,
    write_dpapi_credential,
    write_stop_script,
)
from installer_theme import (  # noqa: E402
    ACCENT,
    BG,
    CARD,
    PANEL,
    SUCCESS,
    TEXT,
    TEXT_DIM,
    TEXT_FAINT,
    ScrollableBody,
    apply_theme,
    build_header,
    center_window,
    create_card,
    load_brand_icon,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_PROXY_EXE_NAME = "roleplay-slim-proxy.exe"


def _find_proxy_exe() -> Path | None:
    """Locate the pre-built proxy executable that the installer ships."""
    # 1. Same directory as the installer exe (production layout)
    installer_dir = _base
    candidate = installer_dir / _PROXY_EXE_NAME
    if candidate.is_file():
        return candidate

    # 2. dist/ subdirectory (development layout)
    candidate = installer_dir / "dist" / _PROXY_EXE_NAME
    if candidate.is_file():
        return candidate

    return None


def _auto_detect_pet_dir() -> str:
    """Best-effort guess of the pet installation directory."""
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

    # Walk E:\ one level deep looking for 若叶睦桌宠_Data
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
class InstallerApp:
    """Tkinter GUI that wraps the CLI installer's core logic."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("若叶睦桌宠 · 上下文优化代理 安装工具")
        apply_theme(self.root)
        self._icon_img = load_brand_icon(self.root, _bundle_dir)

        self._install_running = False
        self._log_queue: list[str] = []
        self._after_id: str | None = None

        self._build_ui()
        self._initial_log()
        center_window(
            self.root,
            preferred_width=760,
            preferred_height=740,
            minimum_width=660,
            minimum_height=560,
        )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """Build all widgets."""
        build_header(
            self.root,
            self._icon_img,
            title="roleplay-slim",
            subtitle="若叶睦桌宠安装工具",
            badge="安全安装",
        )

        self.scroll_body = ScrollableBody(self.root)
        self.scroll_body.pack(fill=tk.BOTH, expand=True)
        self.root.bind_all("<MouseWheel>", self.scroll_body.on_mousewheel)

        dir_card = create_card(
            self.scroll_body.body,
            "桌宠位置",
            "选择若叶睦桌宠的安装目录",
        )
        dir_frame = tk.Frame(dir_card, bg=CARD)
        dir_frame.pack(fill=tk.X)

        self.dir_var = tk.StringVar(value=_auto_detect_pet_dir())
        dir_entry = ttk.Entry(
            dir_frame,
            textvariable=self.dir_var,
            style="Installer.TEntry",
            font=("Consolas", 9),
        )
        dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        browse_btn = ttk.Button(
            dir_frame,
            text="浏览目录",
            command=self._browse_dir,
            style="Installer.TButton",
        )
        browse_btn.pack(side=tk.RIGHT)

        api_card = create_card(
            self.scroll_body.body,
            "模型服务",
            "选择服务商并填写对应的连接信息",
        )
        api_frame = tk.Frame(api_card, bg=CARD)
        api_frame.pack(fill=tk.X)

        api_frame.columnconfigure(1, weight=1)
        ttk.Label(api_frame, text="服务商", style="Installer.TLabel").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 12)
        )
        self.provider_var = tk.StringVar(value="DeepSeek")
        self.provider_combo = ttk.Combobox(
            api_frame,
            textvariable=self.provider_var,
            values=tuple(PROVIDER_PRESETS),
            state="readonly",
            style="Installer.TCombobox",
        )
        self.provider_combo.grid(row=0, column=1, columnspan=2, sticky=tk.EW)
        self.provider_combo.bind("<<ComboboxSelected>>", self._on_provider_change)

        default_provider = PROVIDER_PRESETS["DeepSeek"]
        self.base_url_var = tk.StringVar(value=default_provider["base_url"])
        ttk.Label(api_frame, text="API 地址", style="Installer.TLabel").grid(
            row=1, column=0, sticky=tk.W, padx=(0, 10), pady=(8, 0)
        )
        ttk.Entry(
            api_frame,
            textvariable=self.base_url_var,
            font=("Consolas", 9),
            style="Installer.TEntry",
        ).grid(row=1, column=1, columnspan=2, sticky=tk.EW, pady=(8, 0))

        self.model_var = tk.StringVar(value=default_provider["model"])
        ttk.Label(api_frame, text="模型名称", style="Installer.TLabel").grid(
            row=2, column=0, sticky=tk.W, padx=(0, 10), pady=(8, 0)
        )
        ttk.Entry(
            api_frame,
            textvariable=self.model_var,
            font=("Consolas", 9),
            style="Installer.TEntry",
        ).grid(row=2, column=1, columnspan=2, sticky=tk.EW, pady=(8, 0))

        self.api_var = tk.StringVar(value=read_api_key_from_registry() or "")
        self._show_key = tk.BooleanVar(value=False)
        ttk.Label(api_frame, text="API Key", style="Installer.TLabel").grid(
            row=3, column=0, sticky=tk.W, padx=(0, 10), pady=(8, 0)
        )
        self.api_entry = ttk.Entry(
            api_frame,
            textvariable=self.api_var,
            font=("Consolas", 9),
            style="Installer.TEntry",
        )
        self.api_entry.grid(row=3, column=1, sticky=tk.EW, pady=(8, 0))

        self._toggle_btn = ttk.Button(
            api_frame,
            text="显示",
            width=6,
            command=self._toggle_api_visibility,
            style="Installer.TButton",
        )
        self._toggle_btn.grid(row=3, column=2, padx=(8, 0), pady=(8, 0))
        self._apply_api_mask()

        api_hint = tk.Label(
            api_frame,
            text="DeepSeek 会尝试读取桌宠原有 Key；切换服务商后请填写对应 Key。模型名称留空时沿用桌宠请求。",
            font=("Microsoft YaHei UI", 8),
            fg=TEXT_FAINT,
            bg=CARD,
            wraplength=610,
            justify=tk.LEFT,
        )
        api_hint.grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))

        self._active_provider = "DeepSeek"

        log_card = create_card(
            self.scroll_body.body,
            "安装进度",
            "所有操作都会在这里逐项显示",
        )
        log_frame = tk.Frame(log_card, bg=CARD)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_frame,
            height=9,
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

        # Log colour tags
        self.log_text.tag_configure("ok", foreground=SUCCESS)
        self.log_text.tag_configure("warn", foreground="#B45309")
        self.log_text.tag_configure("error", foreground="#B91C1C")
        self.log_text.tag_configure("info", foreground=ACCENT)
        self.log_text.tag_configure(
            "bold", foreground=TEXT, font=("Consolas", 9, "bold")
        )

        btn_frame = tk.Frame(self.root, bg=BG)
        btn_frame.pack(fill=tk.X, padx=24, pady=(4, 18))

        self.install_btn = ttk.Button(
            btn_frame,
            text="开始安装",
            command=self._start_install,
            style="Installer.Primary.TButton",
        )
        self.install_btn.pack(side=tk.RIGHT, padx=(10, 0))

        close_btn = ttk.Button(
            btn_frame,
            text="关闭",
            command=self.root.destroy,
            style="Installer.TButton",
        )
        close_btn.pack(side=tk.RIGHT)

    def _initial_log(self) -> None:
        """Greeting + pre-flight checks."""
        self._log("roleplay-slim 桌宠安装工具", tag="bold")
        self._log(f"代理端口: 127.0.0.1:{PROXY_PORT}")
        proxy = _find_proxy_exe()
        if proxy:
            self._log(f"代理程序: {proxy.name} ✓", tag="ok")
        else:
            self._log("代理程序: 未找到 (请确保 roleplay-slim-proxy.exe 与本程序在同一目录)", tag="warn")
        self._log("")

    # ------------------------------------------------------------------
    # Logging (thread-safe via tkinter after)
    # ------------------------------------------------------------------
    def _log(self, msg: str, tag: str = "") -> None:
        """Append a line to the log widget. Safe to call from any thread."""
        self._log_queue.append((msg, tag))
        # Schedule a drain on the main thread
        if self._after_id is None:
            self._after_id = self.root.after(50, self._drain_log)

    def _drain_log(self) -> None:
        """Flush queued log lines into the text widget."""
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

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    def _browse_dir(self) -> None:
        path = filedialog.askdirectory(title="选择若叶睦桌宠的安装目录")
        if path:
            self.dir_var.set(path)

    def _toggle_api_visibility(self) -> None:
        self._show_key.set(not self._show_key.get())
        self._apply_api_mask()

    def _apply_api_mask(self) -> None:
        if self._show_key.get():
            self.api_entry.configure(show="")
            self._toggle_btn.configure(text="隐藏")
        else:
            self.api_entry.configure(show="*")
            self._toggle_btn.configure(text="显示")

    def _on_provider_change(self, _event=None) -> None:
        """Apply the selected preset without reusing another provider's key."""
        provider = self.provider_var.get()
        preset = PROVIDER_PRESETS[provider]
        if provider != self._active_provider:
            self.api_var.set("")
        self.base_url_var.set(preset["base_url"])
        self.model_var.set(preset["model"])
        self._active_provider = provider

    def _start_install(self) -> None:
        """Validate inputs, then launch the install in a background thread."""
        if self._install_running:
            return

        # Validate
        pet_dir = self.dir_var.get().strip()
        if not pet_dir:
            messagebox.showwarning("缺少目录", "请先选择桌宠的安装目录。")
            return

        pet_path = Path(pet_dir)
        if not pet_path.is_dir():
            messagebox.showwarning("目录不存在", f"目录不存在:\n{pet_dir}")
            return

        api_key = self.api_var.get().strip()
        if not api_key:
            messagebox.showwarning(
                "API Key 为空",
                "未填入所选服务商的 API Key。\n\n"
                "请填写后再开始安装；API Key 会使用 Windows DPAPI 加密保存。",
            )
            return

        try:
            upstream_base_url = normalize_upstream_base_url(self.base_url_var.get())
        except ValueError as exc:
            messagebox.showwarning("API 地址无效", str(exc))
            return
        upstream_model = self.model_var.get().strip()

        if _find_proxy_exe() is None:
            messagebox.showerror(
                "缺少代理程序",
                "安装器旁没有 roleplay-slim-proxy.exe，未修改任何桌宠文件。",
            )
            return

        # Disable UI during install
        self._install_running = True
        self.install_btn.configure(state=tk.DISABLED, text="正在安装…")

        thread = threading.Thread(
            target=self._run_install,
            args=(pet_path, api_key, upstream_base_url, upstream_model),
            daemon=True,
        )
        thread.start()

    def _run_install(
        self,
        pet_dir: Path,
        api_key: str,
        upstream_base_url: str,
        upstream_model: str,
    ) -> None:
        """Execute the full install pipeline. Runs on a background thread."""
        try:
            self._install(pet_dir, api_key, upstream_base_url, upstream_model)
        except Exception as exc:
            self._log("", tag="")
            self._log(f"✕ 安装失败: {exc}", tag="error")
            self._log("请将上面的日志发送给开发者。", tag="warn")
        finally:
            # Re-enable UI on the main thread
            self.root.after(0, self._install_done)

    def _install_done(self) -> None:
        """Re-enable the install button."""
        self._install_running = False
        self.install_btn.configure(state=tk.NORMAL, text="重新安装")

    def _install(
        self,
        pet_dir: Path,
        api_key: str,
        upstream_base_url: str,
        upstream_model: str,
    ) -> None:
        """Core install logic — mirrors installer/install.py:main() but with
        GUI logging and no interactive prompts."""
        log = self._log  # shorthand
        proxy_src = _find_proxy_exe()
        if proxy_src is None:
            raise RuntimeError("代理程序在安装开始前消失，未修改桌宠文件")

        # --- 1. Locate key files ---
        log("=" * 50, tag="info")
        log("[1/7] 查找关键文件...", tag="bold")

        try:
            level0_path = find_level0(pet_dir)
            log(f"  ✓ level0: {level0_path}", tag="ok")
        except FileNotFoundError as e:
            log(f"  ✕ {e}", tag="error")
            self.root.after(0, lambda: messagebox.showerror(
                "找不到 level0",
                f"在以下目录找不到 level0 文件:\n{pet_dir}\n\n"
                "请确认这是正确的桌宠安装目录。",
            ))
            return

        try:
            metadata_path = find_metadata(pet_dir)
            log(f"  ✓ metadata: {metadata_path}", tag="ok")
        except FileNotFoundError:
            log("  ⚠ global-metadata.dat 未找到，跳过 metadata patch", tag="warn")
            metadata_path = None

        # --- 2. Backup ---
        log("")
        log("[2/7] 备份原文件...", tag="bold")

        backup_l0 = pet_dir / "level0.bak"
        if not backup_l0.exists():
            shutil.copy2(level0_path, backup_l0)
            log("  ✓ level0 → level0.bak", tag="ok")
        else:
            log("  - 备份已存在，跳过", tag="info")

        backup_meta = None
        if metadata_path:
            backup_meta = metadata_path.parent / "global-metadata.dat.bak"
            if not backup_meta.exists():
                shutil.copy2(metadata_path, backup_meta)
                log("  ✓ metadata → global-metadata.dat.bak", tag="ok")
            else:
                log("  - metadata 备份已存在，跳过", tag="info")

        # --- 3. API key status ---
        log("")
        log("[3/7] API Key...", tag="bold")
        if api_key:
            log("  ✓ 已获取并准备加密保存", tag="ok")
        else:
            log("  ⚠ 未提供 API Key，启动脚本中需手动填写", tag="warn")

        # --- 4. Patch level0 ---
        log("")
        log("[4/7] Patch level0 (API 端点 → 代理)...", tag="bold")
        try:
            was_patched = patch_file(level0_path, check_prefix=True, log=log)
            if was_patched:
                log(f"  ✓ level0 已指向 127.0.0.1:{PROXY_PORT}", tag="ok")
        except ValueError:
            log("  ✕ level0 patch 失败！正在从备份恢复...", tag="error")
            shutil.copy2(backup_l0, level0_path)
            log("  - 已从备份恢复", tag="info")
            self.root.after(0, lambda: messagebox.showerror(
                "Patch 失败",
                "level0 文件中未找到 DeepSeek URL。\n"
                "可能原因：桌宠版本不兼容，或文件已被修改。\n"
                "已从备份恢复原文件。",
            ))
            return

        # --- 5. Patch metadata ---
        if metadata_path:
            log("")
            log("[5/7] Patch global-metadata.dat...", tag="bold")
            try:
                was_patched = patch_file(metadata_path, check_prefix=False, log=log)
                if was_patched:
                    log(f"  ✓ metadata 已指向 127.0.0.1:{PROXY_PORT}", tag="ok")
            except ValueError:
                log("  ✕ metadata patch 失败！正在从备份恢复...", tag="error")
                shutil.copy2(backup_meta, metadata_path)
                log("  - 已从备份恢复", tag="info")
                self.root.after(0, lambda: messagebox.showerror(
                    "Metadata Patch 失败",
                    "global-metadata.dat 中未找到 DeepSeek URL。\n"
                    "可能原因：桌宠版本更新了，或文件已被修改。\n"
                    "已从备份恢复原文件。",
                ))
                return
        else:
            log("")
            log("[5/7] Patch metadata — 跳过（未找到）", tag="info")

        # --- 5.5. Patch dynamic registry URL override (newer builds only) ---
        log("")
        log("[5.5/7] Patch 注册表动态 URL 覆盖设置（如果存在）...", tag="bold")
        patch_registry_url(PROXY_PORT, log=log)

        # --- 6. Write proxy config ---
        log("")
        log("[6/7] 生成代理配置...", tag="bold")
        config_dir = pet_dir / "roleplay-slim-proxy"
        config_dir.mkdir(exist_ok=True)
        config_path = config_dir / "config.toml"
        config_path.write_text(
            render_proxy_config(PROXY_PORT, upstream_base_url, upstream_model),
            encoding="utf-8",
        )
        write_stop_script(config_dir, PROXY_PORT)
        log("  ✓ config.toml", tag="ok")
        log(f"  ✓ 上游: {upstream_base_url}", tag="info")
        log(
            f"  ✓ 模型: {upstream_model or '沿用桌宠请求'}",
            tag="info",
        )

        # --- 7. Copy proxy exe ---
        proxy_dst = config_dir / _PROXY_EXE_NAME
        shutil.copy2(proxy_src, proxy_dst)
        log(f"  ✓ {_PROXY_EXE_NAME}", tag="ok")

        # --- 8. Write launcher scripts ---
        log("")
        log("[7/7] 创建启动脚本...", tag="bold")

        proxy_bat = pet_dir / "启动代理.bat"
        write_dpapi_credential(config_dir, api_key)
        proxy_bat.write_text(LAUNCH_PROXY_BAT, encoding="gbk")
        log("  ✓ 启动代理.bat", tag="ok")

        pet_bat = pet_dir / "若叶睦.bat"
        pet_bat.write_text(
            LAUNCH_PET_BAT.replace("{port}", str(PROXY_PORT)),
            encoding="gbk",  # matches the default (no chcp) console code page
        )
        log("  ✓ 若叶睦.bat", tag="ok")

        # --- Done ---
        log("")
        log("=" * 50, tag="info")
        log("  安装完成！", tag="ok")
        log("")
        log("  以后双击「若叶睦.bat」启动即可", tag="bold")
        log(f"  位置: {pet_bat}", tag="info")
        log("")
        log("  备份文件:", tag="info")
        log(f"    {backup_l0}", tag="info")
        if backup_meta:
            log(f"    {backup_meta}", tag="info")
        log("=" * 50, tag="info")

        self.root.after(0, lambda: messagebox.showinfo(
            "安装完成",
            "安装成功！\n\n以后双击「若叶睦.bat」启动桌宠即可。\n"
            "代理会随桌宠自动启停，无需手动管理。",
        ))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    app = InstallerApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
