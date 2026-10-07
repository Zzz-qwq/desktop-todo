@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" pythonw.exe "%~dp0todo_desktop.py"
