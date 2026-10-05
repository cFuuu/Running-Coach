@echo off
rem Start the Running Coach dashboard; the browser opens once the server is ready.
rem Keep this file ASCII-only: cmd.exe parses it in the console codepage (cp950),
rem and UTF-8 Chinese breaks line parsing.
rem
rem Usage (from the project root):
rem     scripts\dashboard                   LAN-reachable (phone), port 8000
rem     scripts\dashboard --host 127.0.0.1 --no-browser
rem Extra args are passed through to api.app; a later --host/--port wins.
rem
rem Overridable env vars:
rem     RC_PYTHON   python.exe of the conda env "rc"
rem                 (default %USERPROFILE%\anaconda3\envs\rc\python.exe)
rem     RC_DB_PATH  SQLite database (default output\running_coach.db)
rem
rem Security: binds 0.0.0.0 by default and the service has NO authentication.
rem Trusted home LAN only, never expose it to the internet (see api/app.py).

setlocal
cd /d "%~dp0.."

if not defined RC_PYTHON set "RC_PYTHON=%USERPROFILE%\anaconda3\envs\rc\python.exe"
if not defined RC_DB_PATH set "RC_DB_PATH=output\running_coach.db"

if not exist "%RC_PYTHON%" (
    echo [error] python of conda env "rc" not found: %RC_PYTHON%
    echo         Set RC_PYTHON to its python.exe.
    exit /b 1
)

"%RC_PYTHON%" -m src.main.python.api.app --db-path "%RC_DB_PATH%" --host 0.0.0.0 %*
