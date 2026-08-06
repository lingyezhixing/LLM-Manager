@echo off
:: Fix working dir to script dir: resolved_db is a relative path ("data/llm_manager.db").
:: Task-scheduler/runas launches default to System32, which created an empty db at
:: C:\Windows\System32\data\llm_manager.db (all-initial WebUI). cd guarantees the db
:: always lands in the deploy dir.
cd /d "%~dp0"
:: Elevate to admin if not already elevated (UAC once). Task-scheduler autostart
:: registered with highest privileges skips this block.
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

if "%1"=="silent" goto silent_start

set "vbsfile=%temp%\%~n0.vbs"
echo Set WshShell = CreateObject("WScript.Shell") > "%vbsfile%"
echo WshShell.Run "cmd /c ""%~f0"" silent", 0, False >> "%vbsfile%"
wscript.exe "%vbsfile%" 2>nul
exit /b

:silent_start
chcp 65001 >nul
title LLM-Manager

:: Force UTF-8 for Python output
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

:: Activate conda env and run. Restart-on-config-change is handled by the built-in
:: parent supervisor (python -m llm_manager self-restarts internally).
call conda activate LLM-Manager
python -m llm_manager
