@echo off
chcp 65001

cmd /k "D:\LLM\LLM-Manager\backend\llama.cpp\llama-server.exe -m E:\models\LLM\GGUF\GLM-4-32B-0414-UD-Q4_K_XL.gguf --jinja -ngl 99 -fa on -c 32768 --temp 0.6 --top-p 0.95 -sm layer -dev cuda0,cuda1 -ts 18,44 --no-mmap --host 127.0.0.1 --port 10004 -a GLM-4-32B-0414"