@echo off
chcp 65001
set CUDA_VISIBLE_DEVICES=1
cmd /k "D:\LLM\LLM-Manager\backend\llama.cpp\llama-server.exe -m E:\models\LLM\Sakura\Sakura-14B-Qwen2.5-v1.0-Q4_K_M.gguf -c 32768 -ngl 999 -fa on --parallel 16 --defrag-thold 0.01 --no-mmap -a Sakura-14B-Qwen2.5-v1.0 --host 127.0.0.1 --port 10009"
pause