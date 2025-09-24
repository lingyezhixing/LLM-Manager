# OpenAI API Router 接口手册

## 接口概览

| 接口路径 | 方法 | 说明 |
|---------|------|------|
| `GET /` | GET | 服务器基本信息 |
| `GET /health` | GET | 健康检查 |
| `GET /v1/models` | GET | 获取模型列表 |
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
curl http://localhost:8000/
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
curl http://localhost:8000/health
```

---

## 模型管理接口

### 模型列表

**接口**: `GET /v1/models`

**功能**: 获取所有可用模型列表（OpenAI兼容）

**响应示例**:
```json
{
  "object": "list",
  "data": [
    {
      "id": "gpt-3.5-turbo",
      "object": "model",
      "created": 1234567890,
      "owned_by": "openai",
      "aliases": ["gpt-3.5", "chatgpt"]
    }
  ]
}
```

**使用示例**:
```bash
curl http://localhost:8000/v1/models
```

### 启动模型

**接口**: `POST /api/models/{model_alias}/start`

**功能**: 启动指定模型

**参数**: `model_alias` - 模型别名

**响应示例**:
```json
{
  "success": true,
  "message": "模型 'gpt-3.5-turbo' 启动成功"
}
```

**使用示例**:
```bash
curl -X POST http://localhost:8000/api/models/gpt-3.5-turbo/start
```

### 停止模型

**接口**: `POST /api/models/{model_alias}/stop`

**功能**: 停止指定模型

**参数**: `model_alias` - 模型别名

**响应示例**:
```json
{
  "success": true,
  "message": "模型 'gpt-3.5-turbo' 停止成功"
}
```

**使用示例**:
```bash
curl -X POST http://localhost:8000/api/models/gpt-3.5-turbo/stop
```

### 重启自动启动模型

**接口**: `POST /api/models/restart-autostart`

**功能**: 重启所有标记为auto_start的模型

**响应示例**:
```json
{
  "success": true,
  "message": "已重启 3 个autostart模型",
  "started_models": ["gpt-3.5-turbo", "claude-2", "llama-2"]
}
```

**使用示例**:
```bash
curl -X POST http://localhost:8000/api/models/restart-autostart
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
curl -X POST http://localhost:8000/api/models/stop-all
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
curl -N http://localhost:8000/api/models/gpt-3.5-turbo/logs/stream
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
curl http://localhost:8000/api/logs/stats
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
  "message": "模型 'gpt-3.5-turbo' 已清理 60 分钟前的日志，删除 100 条"
}
```

**使用示例**:
```bash
# 清空所有日志
curl -X POST http://localhost:8000/api/logs/gpt-3.5-turbo/clear

# 保留最近60分钟的日志
curl -X POST "http://localhost:8000/api/logs/gpt-3.5-turbo/clear?keep_minutes=60"
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
        "device_count": 1,
        "device_name": "NVIDIA GeForce RTX 4090",
        "memory_total": 24576,
        "memory_used": 8192,
        "memory_free": 16384
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
curl http://localhost:8000/api/devices/info
```

---

## OpenAI API 转发接口

### 统一转发

**接口**: `ALL /{path:path}`

**功能**: 统一处理所有OpenAI兼容的API请求

**支持的路由**:
- `POST /v1/chat/completions` - 聊天补全
- `POST /v1/completions` - 文本补全
- `POST /v1/embeddings` - 向量嵌入
- 其他OpenAI API路由

### 聊天补全

**接口**: `POST /v1/chat/completions`

**功能**: 生成聊天响应

**请求示例**:
```json
{
  "model": "gpt-3.5-turbo",
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
  "model": "gpt-3.5-turbo",
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
    "total_tokens": 50
  }
}
```

**使用示例**:
```bash
# 非流式请求
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "你好"}]
  }'

# 流式请求
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
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
  "model": "text-embedding-ada-002",
  "input": "Hello, world!",
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
    }
  ],
  "model": "text-embedding-ada-002",
  "usage": {
    "prompt_tokens": 3,
    "total_tokens": 3
  }
}
```

**使用示例**:
```bash
curl -X POST http://localhost:8000/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "text-embedding-ada-002",
    "input": "Hello world"
  }'
```

---

## 使用示例

### 基本工作流程

```bash
# 1. 检查服务器状态
curl http://localhost:8000/health

# 2. 查看可用模型
curl http://localhost:8000/v1/models

# 3. 启动模型
curl -X POST http://localhost:8000/api/models/gpt-3.5-turbo/start

# 4. 监控日志
curl -N http://localhost:8000/api/models/gpt-3.5-turbo/logs/stream

# 5. 使用模型
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "你好"}]}'

# 6. 清理日志（保留最近10分钟）
curl -X POST "http://localhost:8000/api/logs/gpt-3.5-turbo/clear?keep_minutes=10"

# 7. 停止模型
curl -X POST http://localhost:8000/api/models/gpt-3.5-turbo/stop
```

### JavaScript 客户端示例

```javascript
// 聊天请求
async function chat(message) {
    const response = await fetch('http://localhost:8000/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            model: 'gpt-3.5-turbo',
            messages: [{ role: 'user', content: message }]
        })
    });
    const data = await response.json();
    return data.choices[0].message.content;
}

// 日志监控
function monitorLogs(modelAlias) {
    const eventSource = new EventSource(
        `http://localhost:8000/api/models/${modelAlias}/logs/stream`
    );

    eventSource.onmessage = function(event) {
        const data = JSON.parse(event.data);
        const timestamp = new Date(data.log.timestamp * 1000).toLocaleTimeString();
        console.log(`[${timestamp}] ${data.log.message}`);
    };
}
```

---

## 错误代码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求格式错误或参数无效 |
| 404 | 模型不存在或路由不存在 |
| 500 | 服务器内部错误 |
| 503 | 模型未启动或设备不可用 |

---

*文档生成时间: 2024-09-24*