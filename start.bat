@echo off
chcp 65001 >nul
cd /d %~dp0
echo 正在启动任务清单...
python task_widget.py
pause
