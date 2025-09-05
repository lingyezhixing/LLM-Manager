@echo off
if "%1"=="silent" goto silent_start

set "vbsfile=%temp%\%~n0.vbs"
echo Set WshShell = CreateObject("WScript.Shell") > "%vbsfile%"
echo WshShell.Run "cmd /c ""%~f0"" silent", 0, False >> "%vbsfile%"
wscript.exe "%vbsfile%" 2>nul
exit /b

:silent_start
chcp 65001 >nul
title 中文控制台 (UTF-8编码支持)

:: 设置环境变量以确保 Python 使用 UTF-8 编码
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

:: 创建日志目录
set "logdir=%~dp0logs"
if not exist "%logdir%" mkdir "%logdir%"
for /f "delims=" %%a in ('wmic OS Get localdatetime ^| find "."') do set "datetime=%%a"
set "logfile=%logdir%\%~n0_%datetime:~0,14%.log"

:: 删除旧日志
pushd "%logdir%"
for /f "skip=9 delims=" %%F in ('dir /b /o:-n "%~n0_*.log"') do (
    echo Deleting old log file: "%%F"
    del "%%F"
)
popd

:: 运行程序并记录日志
(
    echo Starting script at %date% %time%
    call conda activate LLM-Manager
    python main.py
    echo Script finished at %date% %time%
) >> "%logfile%" 2>&1