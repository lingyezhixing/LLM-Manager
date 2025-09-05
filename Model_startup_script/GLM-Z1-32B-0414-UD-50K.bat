@echo off
chcp 65001

cmd /k "D:\LLM\LLM-Manager\backend\llama.cpp\llama-server.exe -m E:\models\LLM\GGUF\GLM-Z1-32B-0414-UD-Q4_K_XL.gguf -ngl 99 -fa on -c 51200 -ctk q8_0 -ctv q8_0 --rope-scaling yarn --rope-scale 1.5625 --yarn-orig-ctx 32768 --temp 0.6 --top-k 40 --top-p 0.95 -sm layer -dev cuda0,cuda1 -ts 18,44 --no-mmap --host 127.0.0.1 --port 10010 -a GLM-Z1-32B-0414"