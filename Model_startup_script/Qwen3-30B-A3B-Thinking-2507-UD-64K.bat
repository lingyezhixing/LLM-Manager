@echo off
chcp 65001

cmd /k "D:\LLM\LLM-Manager\backend\llama.cpp\llama-server.exe -m E:\models\LLM\GGUF\Qwen3-30B-A3B-Thinking-2507-UD-Q4_K_XL.gguf --jinja -ngl 99 -fa on -c 65536 -ctk q8_0 -ctv q8_0 --temp 0.6 --top-k 20 --top-p 0.95 --min-p 0 -sm layer -dev cuda0,cuda1 -ts 12,36 --no-mmap --host 127.0.0.1 --port 10003 -a Qwen3-30B-A3B-Thinking-2507"