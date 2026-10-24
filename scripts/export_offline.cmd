@echo off
rem Export the offline dashboard (HTML) to a folder, e.g. an iCloud Drive folder.
rem Keep this file ASCII-only: cmd.exe parses it in the console codepage (cp950).
rem
rem Usage (from the project root):
rem     scripts\export_offline                         default folder
rem     scripts\export_offline D:\some\folder          custom folder
rem     scripts\export_offline --detail-months 12      default folder, 12 months of detail
rem     scripts\export_offline D:\some\folder --detail-months 12
rem If the first argument does not start with "-", it is the destination folder;
rem everything else is passed through to build_dashboard_offline.py.
rem
rem Overridable env vars:
rem     RC_PYTHON             python.exe of the conda env "rc"
rem                           (default %USERPROFILE%\anaconda3\envs\rc\python.exe)
rem     RC_DB_PATH            SQLite database (default output\running_coach.db)
rem     RC_OFFLINE_EXPORT_DIR default destination folder
rem                           (default %USERPROFILE%\iCloudDrive\HTML-decode)

setlocal
cd /d "%~dp0.."

if not defined RC_PYTHON set "RC_PYTHON=%USERPROFILE%\anaconda3\envs\rc\python.exe"
if not defined RC_DB_PATH set "RC_DB_PATH=output\running_coach.db"
if not defined RC_OFFLINE_EXPORT_DIR set "RC_OFFLINE_EXPORT_DIR=%USERPROFILE%\iCloudDrive\HTML-decode"

if not exist "%RC_PYTHON%" (
    echo [error] python of conda env "rc" not found: %RC_PYTHON%
    echo         Set RC_PYTHON to its python.exe.
    exit /b 1
)

set "DEST=%RC_OFFLINE_EXPORT_DIR%"
set "FIRST=%~1"
if not defined FIRST goto args
if "%FIRST:~0,1%"=="-" goto args
set "DEST=%~1"
shift

:args
set "EXTRA="
:loop
if "%~1"=="" goto run
set "EXTRA=%EXTRA% %1"
shift
goto loop

:run
if not exist "%DEST%" mkdir "%DEST%"
"%RC_PYTHON%" scripts\build_dashboard_offline.py --db-path "%RC_DB_PATH%" --copy-to "%DEST%" --dated%EXTRA%
exit /b %errorlevel%
