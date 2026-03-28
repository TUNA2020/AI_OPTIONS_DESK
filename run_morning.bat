@echo off
setlocal
set "PROJECT_DIR=C:\Users\tunap\Desktop\research\AI_OPTIONS_DESK"
if exist "%~dp0run.py" set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

if exist ".venv313\Scripts\activate.bat" (
  call ".venv313\Scripts\activate.bat"
) else if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
)

findstr /i /c:"frontend: react" "config\settings.yaml" >nul
if not errorlevel 1 (
  if exist "frontend-react\package.json" (
    start "AI Options Frontend" cmd /k "cd /d ""%PROJECT_DIR%\frontend-react"" && if not exist node_modules npm.cmd install && npm.cmd run dev -- --host 127.0.0.1 --port 5173"
  )
)

python run.py

if errorlevel 1 (
  echo.
  echo Application exited with an error.
  pause
)
