@echo off
chcp 65001
set CUDA_VISIBLE_DEVICES=1

cmd /k "conda activate lmdeploy && lmdeploy serve api_server E:\models\LLM\Qwen3-14B-AWQ --server-name 127.0.0.1 --server-port 10008 --model-name Qwen3-14B-AWQ --backend turbomind --model-format awq --cache-max-entry-count 0.7 --session-len 2048 --max-concurrent-requests 5 --max-batch-size 5 --chat-template D:\LLM\LLM-Manager\backend\Lmdeploy自定义聊天模板\Qwen3-NoCot-Chat-Template.json --quant-policy 8"