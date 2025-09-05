@echo off
set CUDA_VISIBLE_DEVICES=1
chcp 65001
cmd /k "D:\LLM\LLM-Manager\backend\llama.cpp\llama-server.exe -m E:\models\LLM\GGUF\Tifa-DeepsexV3-14b-Chat-NoCot-0626-Q6.gguf -c 24576 -ngl 99 -fa on --no-mmap --host 127.0.0.1 --port 10005 -a Tifa-DeepsexV3"