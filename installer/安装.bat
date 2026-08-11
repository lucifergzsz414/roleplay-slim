@echo off
chcp 65001 >nul
title 若叶睦桌宠 — roleplay-slim 安装工具

echo.
echo ================================================
echo   若叶睦桌宠 — 上下文优化代理 安装工具
echo   roleplay-slim v0.1.0
echo ================================================
echo.
echo 本工具将为你的若叶睦桌宠安装上下文压缩代理，
echo 降低 LLM API 费用，同时保持角色一致性。
echo.
echo 操作内容：
echo   1. 备份游戏文件（level0）
echo   2. 修改 API 端点指向本地代理
echo   3. 生成代理配置和一键启动脚本
echo.
echo 安装后，双击「若叶睦.bat」启动桌宠即可。
echo ================================================
echo.

:: --- Check Python ---
echo [*] 检查 Python 环境...

set "PYTHON="

:: Try python3 first, then python
where python3 >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON=python3"
) else (
    where python >nul 2>&1
    if %errorlevel% equ 0 (
        set "PYTHON=python"
    )
)

:: If not in PATH, check common install locations
if "%PYTHON%"=="" (
    for /d %%d in ("%LocalAppData%\Programs\Python\Python3*") do (
        if exist "%%d\python.exe" (
            set "PYTHON=%%d\python.exe"
            goto :found_python
        )
    )
    :: Microsoft Store Python
    for /d %%d in ("%LocalAppData%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.*") do (
        if exist "%%d\python.exe" (
            set "PYTHON=%%d\python.exe"
            goto :found_python
        )
    )
)

:found_python
if "%PYTHON%"=="" (
    echo.
    echo [错误] 未检测到 Python！
    echo.
    echo 请先安装 Python 3.10 或更高版本：
    echo   1. 打开 https://www.python.org/downloads/
    echo   2. 下载并安装（务必勾选 "Add Python to PATH"）
    echo   3. 重新运行本安装脚本
    echo.
    pause
    exit /b 1
)

echo   [OK] 找到: %PYTHON%
"%PYTHON%" --version

:: --- Install roleplay-slim ---
echo.
echo [*] 安装 roleplay-slim 库...
"%PYTHON%" -m pip install "roleplay-slim[all]" --quiet
if %errorlevel% neq 0 (
    echo   [warn] pip install 失败，尝试继续...
    echo   如果后续步骤报错，请手动运行: pip install "roleplay-slim[all]"
)

:: --- Run installer ---
echo.
echo [*] 运行安装脚本...
"%PYTHON%" "%~dp0install.py" %*

if %errorlevel% neq 0 (
    echo.
    echo [错误] 安装失败！请截图此窗口内容并反馈。
    pause
    exit /b 1
)

echo.
echo 安装完成！按任意键关闭此窗口...
pause >nul
