@echo off
chcp 65001 >nul
echo ========================================
echo   LLM-Manager 日志流测试工具
echo ========================================
echo.

echo 选择运行模式:
echo 1. 正常模式 (持续监控日志流)
echo 2. 诊断模式 (快速诊断并退出)
echo.

set /p choice=请输入选择 (1/2):

if "%choice%"=="2" (
    echo.
    echo 运行诊断模式...
    echo.
    python test_log_stream.py --model Qwen3-8B-AWQ --diagnose
) else (
    echo.
    echo 启动正常监控模式...
    echo 模型: Qwen3-30B-A3B-Instruct-2507
    echo.
    python test_log_stream.py --model Qwen3-8B-AWQ
)

echo.
echo 测试已完成
pause