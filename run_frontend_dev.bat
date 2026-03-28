@echo off
setlocal
set "PROJECT_DIR=C:\Users\tunap\Desktop\research\AI_OPTIONS_DESK"
if exist "%~dp0frontend-react\package.json" set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%\frontend-react"

if not exist node_modules (
  npm.cmd install
)

npm.cmd run dev -- --host 127.0.0.1 --port 5173
