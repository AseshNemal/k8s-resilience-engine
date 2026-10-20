@echo off
rem Create (or reuse) the local kind research cluster and write a kubeconfig for the app container.
rem Run `scripts\cluster-up.cmd --help` for the options.
rem
rem This is only a launcher: the logic lives in cluster_up.py so Windows, macOS and Linux
rem (scripts/cluster-up.sh) all run the same code. The py launcher is preferred because the
rem plain `python` on a fresh Windows install is often a Microsoft Store stub.
where py >nul 2>nul
if not errorlevel 1 goto usepy
where python >nul 2>nul
if not errorlevel 1 goto usepython
echo ERROR: Python 3.9 or newer is required to run this script. Install it from https://www.python.org/downloads/ 1>&2
exit /b 1

:usepy
py -3 "%~dp0cluster_up.py" %*
exit /b %errorlevel%

:usepython
python "%~dp0cluster_up.py" %*
exit /b %errorlevel%
