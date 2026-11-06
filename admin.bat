@echo off
rem 在浏览器打开本机管理页面（服务需已通过 start.bat 启动）
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m wiki_mcp admin-url --open -c config.yaml
) else (
  python -m wiki_mcp admin-url --open -c config.yaml
)
