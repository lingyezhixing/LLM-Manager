@echo off
:: 提权:非管理员 → PowerShell 以管理员身份重新拉起自身(UAC 一次)。
:: 开机自启(任务计划程序)已注册为最高权限则直接跳过此段。
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
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

:: 设置环境变量以确保 Python 使用 UTF-8 编码
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

:: 激活conda环境并运行。重启由程序内置的 parent 监督器处理(python -m llm_manager 即自重启)。
call conda activate LLM-Manager
python -m llm_manager
