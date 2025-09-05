@echo off
chcp 65001

cmd /k "D:\LLM\LLM-Manager\backend\llama.cpp\llama-server.exe -m E:\models\LLM\GGUF\Baichuan-M2-32B-IQ4_XS.gguf --jinja -ngl 99 -fa on -c 16384 -sm layer -dev cuda0,cuda1 -ts 19,46 --no-mmap --host 127.0.0.1 --port 10011 -a Baichuan-M2-32B"