@echo off
setlocal
set "PROJECT_DIR=C:\Users\tunap\Desktop\research\AI_OPTIONS_DESK"
if exist "%~dp0run.py" set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

set "ACTIVATE_CMD="
if exist ".venv313\Scripts\activate.bat" (
  set "ACTIVATE_CMD=call .venv313\Scripts\activate.bat"
) else if exist ".venv\Scripts\activate.bat" (
  set "ACTIVATE_CMD=call .venv\Scripts\activate.bat"
)

start "AI Options Backend" cmd /k "cd /d ""%PROJECT_DIR%"" && %ACTIVATE_CMD% && python run.py"
start "AI Options Frontend" cmd /k "cd /d ""%PROJECT_DIR%\frontend-react"" && if not exist node_modules npm.cmd install && npm.cmd run dev -- --host 127.0.0.1 --port 5173"

echo Backend started in a new window.
echo Frontend started in a new window.
echo Open http://127.0.0.1:5173
