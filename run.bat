@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
".venv\Scripts\python.exe" cli.py %*
set code=%errorlevel%
popd
exit /b %code%
