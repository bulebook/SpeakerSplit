@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
".venv\Scripts\pythonw.exe" main.py
popd
