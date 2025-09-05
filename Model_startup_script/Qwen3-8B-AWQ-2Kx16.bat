@echo off
chcp 65001
set CUDA_VISIBLE_DEVICES=1

cmd /k "conda activate lmdeploy && lmdeploy serve api_server E:\models\LLM\Qwen3-8B-AWQ --server-name 127.0.0.1 --server-port 10007 --model-name Qwen3-8B-AWQ --backend turbomind --model-format awq --cache-max-entry-count 0.95 --session-len 2048 --max-concurrent-requests 16 --max-batch-size 16 --chat-template D:\LLM\LLM-Manager\backend\Lmdeploy自定义聊天模板\Qwen3-NoCot-Chat-Template.json"