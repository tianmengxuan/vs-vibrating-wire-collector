@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 当前目录: %cd%
echo.

echo [1/3] 清理旧构建...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "VS振弦数据采集器.spec" del "VS振弦数据采集器.spec"

echo [2/3] PyInstaller 打包中...
python -m PyInstaller --onefile --console --name "VS振弦数据采集器" --hidden-import pymysql --hidden-import tkinter --hidden-import tkinter.ttk --hidden-import tkinter.messagebox --hidden-import _tkinter --hidden-import queue --hidden-import threading --hidden-import asyncio --hidden-import json --hidden-import argparse --hidden-import signal --hidden-import traceback --hidden-import config --hidden-import logger_config --collect-submodules database --collect-submodules calculator --collect-submodules protocol --collect-submodules tcp_server --collect-submodules remote_config main.py

echo.
if exist "dist\VS振弦数据采集器.exe" (
    echo [3/3] 打包成功!
    for %%f in ("dist\VS振弦数据采集器.exe") do echo 文件: %%~ff  大小: %%~zf 字节
) else (
    echo [3/3] 打包失败! 请检查是否安装了 PyInstaller: pip install pyinstaller
)
pause
