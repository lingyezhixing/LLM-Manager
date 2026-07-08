@echo off
chcp 65001 >nul
title Dev-Backend (LLM-Manager)

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

call conda activate LLM-Manager
uvicorn llm_manager.app:create_dev_app --factory --reload --host 0.0.0.0 --port 8080 --log-level debug
pause
