@echo off
rem Windows 启动脚本：强制 UTF-8，优先使用项目内 .venv
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m wiki_mcp serve -c config.yaml %*
) else (
  python -m wiki_mcp serve -c config.yaml %*
)
