# LLM-Manager API 接口文档

## 接口概览

| 接口路径 | 方法 | 说明 |
|---------|------|------|
| `GET /` | GET | 服务器基本信息 |
| `GET /health` | GET | 健康检查 |
| `GET /v1/models` | GET | 获取模型列表 (OpenAI兼容) |
| `GET /api/models/{model_alias}/info` | GET | 获取模型详细信息 |
| `POST /api/models/{model_alias}/start` | POST | 启动指定模型 |
| `POST /api/models/{model_alias}/stop` | POST | 停止指定模型 |
| `POST /api/models/restart-autostart` | POST | 重启所有自动启动模型 |
| `POST /api/models/stop-all` | POST | 停止所有模型 |
| `GET /api/models/{model_alias}/logs/stream` | GET | 流式获取模型日志 |
| `GET /api/logs/stats` | GET | 获取日志统计信息 |
| `POST /api/logs/{model_alias}/clear` | POST | 清理指定模型日志 |
| `GET /api/devices/info` | GET | 获取设备信息 |
| `ALL /{path:path}` | ALL | OpenAI API 统一转发 |

---

## 系统接口

### 服务器信息

**接口**: `GET /`

**功能**: 获取服务器基本信息

**响应示例**:
```json
{
  "message": "LLM-Manager API Server",
  "version": "1.0.0",
  "models_url": "/v1/models"
}
```

**使用示例**:
```bash
curl http://localhost:8080/
```

### 健康检查

**接口**: `GET /health`

**功能**: 检查服务器和模型运行状态

**响应示例**:
```json
{
  "status": "healthy",
  "models_count": 5,
  "running_models": 3
}
```

**使用示例**:
```bash
curl http://localhost:8080/health
```

---

## 模型管理接口

### 模型列表

**接口**: `GET /v1/models`

**功能**: 获取所有可用模型列表（OpenAI兼容格式）

**响应示例**:
```json
{
  "object": "list",
  "data": [
    {
      "id": "Qwen3-Coder-30B-A3B-Instruct-UD-64K",
      "object": "model",
      "created": 1234567890,
      "owned_by": "user",
      "aliases": ["Qwen3-Coder-30B-A3B-Instruct-64K", "Qwen3-Coder-30B-A3B-Instruct"]
    }
  ]
}
```

**使用示例**:
```bash
curl http://localhost:8080/v1/models
```

### 模型详细信息

**接口**: `GET /api/models/{model_alias}/info`

**功能**: 获取指定模型的详细信息，包括运行状态和待处理请求数

**参数**:
- `model_alias` - 模型别名，或使用 "all-models" 获取所有模型信息

**单个模型响应示例**:
```json
{
  "success": true,
  "model": {
    "model_name": "Qwen3-Coder-30B-A3B-Instruct-UD-64K",
    "aliases": ["Qwen3-Coder-30B-A3B-Instruct-64K", "Qwen3-Coder-30B-A3B-Instruct"],
    "status": "routing",
    "pid": 12345,
    "idle_time_sec": 120,
    "mode": "Chat",
    "is_available": true,
    "current_bat_path": "Model_startup_script\\Qwen3-Coder-30B-A3B-Instruct-UD-64K.bat",
    "config_source": "RTX4060-V100",
    "failure_reason": null,
    "pending_requests": 2
  }
}
```

**所有模型响应示例**:
```json
{
  "success": true,
  "models": {
    "Qwen3-Coder-30B-A3B-Instruct-UD-64K": {
      "model_name": "Qwen3-Coder-30B-A3B-Instruct-UD-64K",
      "status": "routing",
      "pending_requests": 2
    }
  },
  "total_models": 5,
  "running_models": 3,
  "total_pending_requests": 4
}
```

**使用示例**:
```bash
# 获取单个模型信息
curl http://localhost:8080/api/models/Qwen3-Coder-30B/info

# 获取所有模型信息
curl http://localhost:8080/api/models/all-models/info
```

### 启动模型

**接口**: `POST /api/models/{model_alias}/start`

**功能**: 启动指定模型

**参数**: `model_alias` - 模型别名

**响应示例**:
```json
{
  "success": true,
  "message": "模型 'Qwen3-Coder-30B-A3B-Instruct-UD-64K' 启动成功"
}
```

**错误响应示例**:
```json
{
  "success": false,
  "message": "设备不可用或资源不足"
}
```

**使用示例**:
```bash
curl -X POST http://localhost:8080/api/models/Qwen3-Coder-30B/start
```

### 停止模型

**接口**: `POST /api/models/{model_alias}/stop`

**功能**: 停止指定模型

**参数**: `model_alias` - 模型别名

**响应示例**:
```json
{
  "success": true,
  "message": "模型 'Qwen3-Coder-30B-A3B-Instruct-UD-64K' 停止成功"
}
```

**使用示例**:
```bash
curl -X POST http://localhost:8080/api/models/Qwen3-Coder-30B/stop
```

### 重启自动启动模型

**接口**: `POST /api/models/restart-autostart`

**功能**: 重启所有标记为auto_start的模型

**响应示例**:
```json
{
  "success": true,
  "message": "已重启 3 个autostart模型",
  "started_models": ["Qwen3-Coder-30B-A3B-Instruct-UD-64K", "Qwen3-14B-AWQ-32K"]
}
```

**使用示例**:
```bash
curl -X POST http://localhost:8080/api/models/restart-autostart
```

### 停止所有模型

**接口**: `POST /api/models/stop-all`

**功能**: 停止所有运行的模型

**响应示例**:
```json
{
  "success": true,
  "message": "所有模型已关闭"
}
```

**使用示例**:
```bash
curl -X POST http://localhost:8080/api/models/stop-all
```

---

## 日志管理接口

### 流式日志

**接口**: `GET /api/models/{model_alias}/logs/stream`

**功能**: 实时流式获取模型日志（SSE格式）

**参数**: `model_alias` - 模型别名

**响应格式**:
```
data: {"type": "historical", "log": {"timestamp": 1634567890, "message": "模型启动中..."}}
data: {"type": "historical_complete"}
data: {"type": "realtime", "log": {"timestamp": 1634567891, "message": "模型已就绪"}}
```

**消息类型**:
- `historical`: 历史日志
- `historical_complete`: 历史日志发送完成
- `realtime`: 实时日志
- `stream_end`: 流结束
- `error`: 错误信息

**使用示例**:
```bash
curl -N http://localhost:8080/api/models/Qwen3-Coder-30B/logs/stream
```

### 日志统计

**接口**: `GET /api/logs/stats`

**功能**: 获取日志统计信息

**响应示例**:
```json
{
  "success": true,
  "stats": {
    "total_models": 5,
    "total_log_entries": 1000,
    "models_with_logs": 3
  }
}
```

**使用示例**:
```bash
curl http://localhost:8080/api/logs/stats
```

### 清理日志

**接口**: `POST /api/logs/{model_alias}/clear`

**功能**: 清理指定模型的日志

**参数**:
- `model_alias` - 模型别名
- `keep_minutes` (查询参数) - 保留最近多少分钟的日志，默认为0（清空所有）

**响应示例**:
```json
{
  "success": true,
  "message": "模型 'Qwen3-Coder-30B-A3B-Instruct-UD-64K' 已清理 60 分钟前的日志，删除 100 条"
}
```

**使用示例**:
```bash
# 清空所有日志
curl -X POST http://localhost:8080/api/logs/Qwen3-Coder-30B/clear

# 保留最近60分钟的日志
curl -X POST "http://localhost:8080/api/logs/Qwen3-Coder-30B/clear?keep_minutes=60"
```

---

## 设备管理接口

### 设备信息

**接口**: `GET /api/devices/info`

**功能**: 获取所有可用设备的信息

**响应示例**:
```json
{
  "success": true,
  "devices": {
    "cuda": {
      "online": true,
      "info": {
        "device_count": 2,
        "devices": [
          {
            "name": "NVIDIA RTX 4060",
            "memory_total": 8192,
            "memory_used": 2048,
            "memory_free": 6144,
            "temperature": 65
          },
          {
            "name": "NVIDIA V100",
            "memory_total": 16384,
            "memory_used": 8192,
            "memory_free": 8192,
            "temperature": 45
          }
        ]
      }
    },
    "cpu": {
      "online": true,
      "info": {
        "cpu_count": 16,
        "memory_total": 32768,
        "memory_used": 8192,
        "memory_free": 24576
      }
    }
  }
}
```

**使用示例**:
```bash
curl http://localhost:8080/api/devices/info
```

---

## OpenAI API 转发接口

### 统一转发

**接口**: `ALL /{path:path}`

**功能**: 统一处理所有OpenAI兼容的API请求，支持自动模型启动和token记录

**支持的路由**:
- `POST /v1/chat/completions` - 聊天补全
- `POST /v1/completions` - 文本补全
- `POST /v1/embeddings` - 向量嵌入
- `POST /v1/rerank` - 重排序
- 其他OpenAI API路由

### 聊天补全

**接口**: `POST /v1/chat/completions`

**功能**: 生成聊天响应，支持流式和非流式输出

**请求示例**:
```json
{
  "model": "Qwen3-Coder-30B-A3B-Instruct-UD-64K",
  "messages": [
    {"role": "user", "content": "你好，请介绍一下自己"}
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 1000
}
```

**响应示例**:
```json
{
  "id": "chatcmpl-123",
  "object": "chat.completion",
  "created": 1677652288,
  "model": "Qwen3-Coder-30B-A3B-Instruct-UD-64K",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "你好！我是一个AI助手，很高兴为你提供帮助..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 20,
    "completion_tokens": 30,
    "total_tokens": 50,
    "cache_n": 5,
    "prompt_n": 15
  }
}
```

**流式响应示例**:
```
data: {"id": "chatcmpl-123", "object": "chat.completion.chunk", "created": 1677652288, "model": "Qwen3-Coder-30B-A3B-Instruct-UD-64K", "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": null}]}
data: {"id": "chatcmpl-123", "object": "chat.completion.chunk", "created": 1677652288, "model": "Qwen3-Coder-30B-A3B-Instruct-UD-64K", "choices": [{"index": 0, "delta": {"content": "你好"}, "finish_reason": null}]}
data: {"id": "chatcmpl-123", "object": "chat.completion.chunk", "created": 1677652288, "model": "Qwen3-Coder-30B-A3B-Instruct-UD-64K", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
data: [DONE]
```

**使用示例**:
```bash
# 非流式请求
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-Coder-30B-A3B-Instruct-UD-64K",
    "messages": [{"role": "user", "content": "你好"}]
  }'

# 流式请求
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-Coder-30B-A3B-Instruct-UD-64K",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### 向量嵌入

**接口**: `POST /v1/embeddings`

**功能**: 生成文本的向量嵌入

**请求示例**:
```json
{
  "model": "Qwen3-Embedding-8B",
  "input": ["Hello, world!", "This is a test"],
  "encoding_format": "float"
}
```

**响应示例**:
```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "index": 0,
      "embedding": [0.1, 0.2, 0.3, ...]
    },
    {
      "object": "embedding",
      "index": 1,
      "embedding": [0.4, 0.5, 0.6, ...]
    }
  ],
  "model": "Qwen3-Embedding-8B",
  "usage": {
    "prompt_tokens": 10,
    "total_tokens": 10
  }
}
```

**使用示例**:
```bash
curl -X POST http://localhost:8080/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-Embedding-8B",
    "input": ["Hello world"]
  }'
```

### 重排序

**接口**: `POST /v1/rerank`

**功能**: 对文档列表进行重排序

**请求示例**:
```json
{
  "model": "bge-reranker-v2-m3",
  "query": "What is artificial intelligence?",
  "documents": [
    "Artificial intelligence is a branch of computer science.",
    "Machine learning is a subset of AI.",
    "Deep learning uses neural networks."
  ]
}
```

**响应示例**:
```json
{
  "object": "rerank",
  "results": [
    {
      "index": 0,
      "document": "Artificial intelligence is a branch of computer science.",
      "relevance_score": 0.95
    },
    {
      "index": 2,
      "document": "Deep learning uses neural networks.",
      "relevance_score": 0.87
    },
    {
      "index": 1,
      "document": "Machine learning is a subset of AI.",
      "relevance_score": 0.82
    }
  ]
}
```

**使用示例**:
```bash
curl -X POST http://localhost:8080/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{
    "model": "bge-reranker-v2-m3",
    "query": "What is AI?",
    "documents": ["AI is computer science", "ML is part of AI"]
  }'
```

---

## 使用示例

### 基本工作流程

```bash
# 1. 检查服务器状态
curl http://localhost:8080/health

# 2. 查看可用模型
curl http://localhost:8080/v1/models

# 3. 获取模型详细信息
curl http://localhost:8080/api/models/Qwen3-Coder-30B/info

# 4. 启动模型
curl -X POST http://localhost:8080/api/models/Qwen3-Coder-30B/start

# 5. 监控模型日志
curl -N http://localhost:8080/api/models/Qwen3-Coder-30B/logs/stream

# 6. 使用模型进行聊天
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-Coder-30B-A3B-Instruct-UD-64K",
    "messages": [{"role": "user", "content": "你好"}]
  }'

# 7. 查看待处理请求数
curl http://localhost:8080/api/models/Qwen3-Coder-30B/info

# 8. 清理日志（保留最近10分钟）
curl -X POST "http://localhost:8080/api/logs/Qwen3-Coder-30B/clear?keep_minutes=10"

# 9. 停止模型
curl -X POST http://localhost:8080/api/models/Qwen3-Coder-30B/stop
```

### JavaScript 客户端示例

```javascript
// 聊天请求
async function chat(message, model) {
    const response = await fetch('http://localhost:8080/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            model: model,
            messages: [{ role: 'user', content: message }]
        })
    });
    const data = await response.json();
    return data.choices[0].message.content;
}

// 流式聊天请求
async function streamChat(message, model, onChunk) {
    const response = await fetch('http://localhost:8080/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            model: model,
            messages: [{ role: 'user', content: message }],
            stream: true
        })
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');

        for (const line of lines) {
            if (line.startsWith('data: ')) {
                const data = line.slice(6);
                if (data === '[DONE]') break;

                try {
                    const parsed = JSON.parse(data);
                    onChunk(parsed);
                } catch (e) {
                    // 忽略解析错误
                }
            }
        }
    }
}

// 日志监控
function monitorLogs(modelAlias) {
    const eventSource = new EventSource(
        `http://localhost:8080/api/models/${modelAlias}/logs/stream`
    );

    eventSource.onmessage = function(event) {
        const data = JSON.parse(event.data);
        const timestamp = new Date(data.log.timestamp * 1000).toLocaleTimeString();
        console.log(`[${timestamp}] ${data.log.message}`);
    };

    eventSource.onerror = function() {
        console.error('日志监控连接错误');
        eventSource.close();
    };
}

// 获取模型状态
async function getModelInfo(modelAlias) {
    const response = await fetch(`http://localhost:8080/api/models/${modelAlias}/info`);
    const data = await response.json();
    return data;
}
```

### Python 客户端示例

```python
import requests
import json
from typing import List, Dict, Any

class LLMManagerClient:
    def __init__(self, base_url: str = "http://localhost:8080"):
        self.base_url = base_url

    def chat(self, messages: List[Dict[str, str]], model: str, stream: bool = False) -> Dict[str, Any]:
        """发送聊天请求"""
        data = {
            "model": model,
            "messages": messages,
            "stream": stream
        }

        response = requests.post(
            f"{self.base_url}/v1/chat/completions",
            json=data,
            headers={"Content-Type": "application/json"}
        )

        return response.json()

    def get_model_info(self, model_alias: str) -> Dict[str, Any]:
        """获取模型信息"""
        response = requests.get(f"{self.base_url}/api/models/{model_alias}/info")
        return response.json()

    def start_model(self, model_alias: str) -> Dict[str, Any]:
        """启动模型"""
        response = requests.post(f"{self.base_url}/api/models/{model_alias}/start")
        return response.json()

    def stop_model(self, model_alias: str) -> Dict[str, Any]:
        """停止模型"""
        response = requests.post(f"{self.base_url}/api/models/{model_alias}/stop")
        return response.json()

    def get_device_info(self) -> Dict[str, Any]:
        """获取设备信息"""
        response = requests.get(f"{self.base_url}/api/devices/info")
        return response.json()

# 使用示例
client = LLMManagerClient()

# 启动模型并聊天
model_name = "Qwen3-Coder-30B-A3B-Instruct-UD-64K"
client.start_model(model_name)

response = client.chat([
    {"role": "user", "content": "你好，请简单介绍一下LLM-Manager"}
], model_name)

print(response["choices"][0]["message"]["content"])
```

---

## 错误代码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 204 | 成功（无内容）|
| 400 | 请求格式错误或参数无效 |
| 404 | 模型不存在或路由不存在 |
| 500 | 服务器内部错误 |
| 503 | 模型未启动或设备不可用 |

### 错误响应格式

```json
{
  "success": false,
  "message": "错误描述信息",
  "error": "详细错误信息（可选）"
}
```

---

## 特性说明

### 1. 自动模型管理
- 模型按需启动，无需手动管理
- 支持多设备和资源分配
- 空闲自动卸载机制

### 2. 智能路由
- 支持模型别名解析
- 自动选择最佳设备配置
- 请求负载均衡

### 3. Token记录
- 自动提取和记录token使用情况
- 支持流式和非流式响应
- 缓存和上下文统计

### 4. 实时监控
- 模型状态实时监控
- 设备状态监控
- 流式日志推送

### 5. OpenAI兼容
- 完全兼容OpenAI API格式
- 支持多种模型模式（Chat、Embedding、Reranker）
- 支持流式和非流式响应

---

*文档生成时间: 2024-09-25*
*API版本: 1.0.0*