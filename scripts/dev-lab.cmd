@echo off
REM Start the Member 4 Development Lab (offline, development only).
REM Usage: scripts\dev-lab.cmd [port]   (default 8741)
setlocal
set PORT=%1
if "%PORT%"=="" set PORT=8741
set PYTHON=.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python
echo Starting the Member 4 Development Lab on http://127.0.0.1:%PORT% (offline; no cluster is read or written)
set DEV_LAB_PORT=%PORT%
"%PYTHON%" -m experiments.member4_dev_lab
