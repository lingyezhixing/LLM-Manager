# LLM-Manager API 接口文档

## 📋 文档说明

本文档详细描述了 LLM-Manager v1.0.0 的所有 API 接口，包括基础信息、请求示例和返回结构。

## 🔗 基础信息

- **服务器地址**: `http://localhost:8080`
- **API版本**: `1.0.0`
- **协议**: HTTP/1.1
- **数据格式**: JSON
- **编码**: UTF-8

---

## 📖 接口列表

### 1. 根路径

**接口地址**: `GET /`

**功能**: 获取API服务器基本信息

**请求示例**:
```bash
curl http://localhost:8080/
```

**返回结构**:
```json
{
  "message": "LLM-Manager API Server",
  "version": "1.0.0",
  "models_url": "/v1/models"
}
```

---

### 2. 健康检查

**接口地址**: `GET /health`

**功能**: 检查API服务器和模型状态

**请求示例**:
```bash
curl http://localhost:8080/health
```

**返回结构**:
```json
{
  "status": "healthy",
  "models_count": 13,
  "running_models": 0
}
```

**返回字段说明**:
- `status`: 服务器状态 (`healthy`/`unhealthy`)
- `models_count`: 配置的模型总数
- `running_models`: 正在运行的模型数量

---

### 3. 模型列表 (OpenAI兼容)

**接口地址**: `GET /v1/models`

**功能**: 获取所有可用模型列表 (OpenAI格式)

**请求示例**:
```bash
curl http://localhost:8080/v1/models
```

**返回结构**:
```json
{
  "object": "list",
  "data": [
    {
      "id": "Qwen3-Coder-30B-A3B-Instruct-64K",
      "object": "model",
      "created": 1758799338,
      "owned_by": "user",
      "aliases": ["Qwen3-Coder-30B-A3B-Instruct-64K", "Qwen3-Coder-30B-A3B-Instruct"],
      "mode": "Chat"
    }
  ]
}
```

**返回字段说明**:
- `id`: 模型唯一标识符
- `object`: 对象类型 (固定为 `model`)
- `created`: 创建时间戳
- `owned_by": 所有者 (固定为 `user`)
- `aliases`: 模型别名列表
- `mode`: 模型模式 (`Chat`/`Base`/`Embedding`/`Reranker`)

---

### 4. 获取模型信息

**接口地址**: `GET /api/models/{model_alias}/info`

**功能**: 获取指定模型的详细信息，支持 `all-models` 获取全部模型信息

**路径参数**:
- `model_alias`: 模型别名或 `all-models` (获取全部模型)

**请求示例**:
```bash
# 获取所有模型信息
curl http://localhost:8080/api/models/all-models/info

# 获取特定模型信息
curl http://localhost:8080/api/models/Qwen3-8B-AWQ/info
```

**返回结构**:
```json
{
  "success": true,
  "models": {
    "Qwen3-8B-AWQ": {
      "model_name": "Qwen3-8B-AWQ",
      "aliases": ["Qwen3-8B-AWQ"],
      "status": "stopped",
      "pid": null,
      "idle_time_sec": "N/A",
      "mode": "Chat",
      "is_available": true,
      "current_bat_path": "Model_startup_script\\Qwen3-8B-AWQ-32Kx16.bat",
      "config_source": "V100",
      "failure_reason": null,
      "pending_requests": 0
    }
  },
  "total_models": 13,
  "running_models": 0,
  "total_pending_requests": 0
}
```

**返回字段说明**:
- `model_name`: 模型主要名称
- `aliases`: 模型别名列表
- `status`: 模型状态 (`stopped`/`starting`/`routing`/`failed`)
- `pid`: 进程ID (null表示未运行)
- `idle_time_sec`: 空闲时间 (秒)
- `mode`: 模型模式
- `is_available`: 是否可用
- `current_bat_path`: 启动脚本路径
- `config_source`: 配置来源
- `failure_reason`: 失败原因 (null表示正常)
- `pending_requests`: 待处理请求数

---

### 5. 启动模型

**接口地址**: `POST /api/models/{model_alias}/start`

**功能**: 启动指定模型

**路径参数**:
- `model_alias`: 模型别名

**请求示例**:
```bash
curl -X POST http://localhost:8080/api/models/Qwen3-8B-AWQ/start
```

**返回结构**:
```json
{
  "success": true,
  "message": "模型 Qwen3-8B-AWQ 启动成功"
}
```

**返回字段说明**:
- `success`: 操作是否成功
- `message`: 操作结果消息

---

### 6. 停止模型

**接口地址**: `POST /api/models/{model_alias}/stop`

**功能**: 停止指定模型

**路径参数**:
- `model_alias`: 模型别名

**请求示例**:
```bash
curl -X POST http://localhost:8080/api/models/Qwen3-8B-AWQ/stop
```

**返回结构**:
```json
{
  "success": true,
  "message": "模型 Qwen3-8B-AWQ 停止成功"
}
```

**返回字段说明**:
- `success`: 操作是否成功
- `message`: 操作结果消息

---

### 7. 流式获取模型日志

**接口地址**: `GET /api/models/{model_alias}/logs/stream`

**功能**: 流式获取模型控制台日志，支持实时推送

**路径参数**:
- `model_alias`: 模型别名

**请求示例**:
```bash
curl -N http://localhost:8080/api/models/Qwen3-8B-AWQ/logs/stream
```

**返回格式**: Server-Sent Events (SSE)

```
data: {"type": "historical", "log": "[timestamp] log message"}

data: {"type": "historical_complete"}

data: {"type": "realtime", "log": "[timestamp] real-time log message"}
```

**事件类型说明**:
- `historical`: 历史日志
- `historical_complete`: 历史日志发送完成
- `realtime`: 实时日志
- `stream_end`: 流结束
- `error`: 错误信息

---

### 8. 重启所有自动启动模型

**接口地址**: `POST /api/models/restart-autostart`

**功能**: 重启所有配置为自动启动的模型

**请求示例**:
```bash
curl -X POST http://localhost:8080/api/models/restart-autostart
```

**返回结构**:
```json
{
  "success": true,
  "message": "已重启 X 个autostart模型",
  "started_models": ["model1", "model2"]
}
```

**返回字段说明**:
- `success`: 操作是否成功
- `message`: 操作结果消息
- `started_models`: 成功启动的模型列表

---

### 9. 停止所有模型

**接口地址**: `POST /api/models/stop-all`

**功能**: 停止所有正在运行的模型

**请求示例**:
```bash
curl -X POST http://localhost:8080/api/models/stop-all
```

**返回结构**:
```json
{
  "success": true,
  "message": "所有模型已关闭"
}
```

**返回字段说明**:
- `success`: 操作是否成功
- `message`: 操作结果消息

---

### 10. 获取设备信息

**接口地址**: `GET /api/devices/info`

**功能**: 获取所有计算设备的状态信息

**请求示例**:
```bash
curl http://localhost:8080/api/devices/info
```

**返回结构**:
```json
{
  "success": true,
  "devices": {
    "CPU": {
      "online": true,
      "info": {
        "device_type": "CPU",
        "memory_type": "RAM",
        "total_memory_mb": 31966,
        "available_memory_mb": 21410,
        "used_memory_mb": 10555,
        "usage_percentage": 8.4,
        "temperature_celsius": null
      }
    },
    "rtx 4060": {
      "online": true,
      "info": {
        "device_type": "GPU",
        "memory_type": "VRAM",
        "total_memory_mb": 8188,
        "available_memory_mb": 6901,
        "used_memory_mb": 1057,
        "usage_percentage": 1.0,
        "temperature_celsius": 57.0
      }
    }
  }
}
```

**返回字段说明**:
- `online`: 设备是否在线
- `device_type`: 设备类型 (`CPU`/`GPU`等，由于设备类型过多，未来可能出现其他类型，所以不要写死，用`device_type`字段，获取到什么就是什么，内存类型同理)
- `memory_type`: 内存类型 (`RAM`/`VRAM`等)
- `total_memory_mb`: 总内存 (MB)
- `available_memory_mb`: 可用内存 (MB)
- `used_memory_mb`: 已用内存 (MB)
- `usage_percentage`: 使用率百分比（非内存使用率百分比，这是设备使用率百分比，如需内存使用率百分比请自行计算）
- `temperature_celsius`: 温度 (摄氏度，一些不支持检测的设备这个字段可能为None)

---

### 11. 获取日志统计

**接口地址**: `GET /api/logs/stats`

**功能**: 获取模型控制台日志统计信息

**请求示例**:
```bash
curl http://localhost:8080/api/logs/stats
```

**返回结构**:
```json
{
  "success": true,
  "stats": {
    "total_models": 13,
    "total_log_entries": 0,
    "total_subscribers": 0,
    "model_stats": {
      "Qwen3-8B-AWQ": {
        "log_count": 0,
        "subscriber_count": 0
      }
    }
  }
}
```

**返回字段说明**:
- `total_models`: 总模型数
- `total_log_entries`: 总日志条目数
- `total_subscribers`: 总订阅者数
- `model_stats`: 各模型统计信息
  - `log_count`: 日志条目数
  - `subscriber_count`: 订阅者数

---

### 12. 清理模型日志

**接口地址**: `POST /api/logs/{model_alias}/clear`

**功能**: 清理模型控制台日志，支持选择性保留

**路径参数**:
- `model_alias`: 模型别名

**查询参数**:
- `keep_minutes` (可选，默认0): 保留最近多少分钟的日志，0表示清空所有

**请求示例**:
```bash
# 清空所有日志
curl -X POST http://localhost:8080/api/logs/Qwen3-8B-AWQ/clear

# 保留最近10分钟的日志
curl -X POST "http://localhost:8080/api/logs/Qwen3-8B-AWQ/clear?keep_minutes=10"
```

**返回结构**:
```json
{
  "success": true,
  "message": "模型 'Qwen3-8B-AWQ' 所有日志已清空"
}
```

**返回字段说明**:
- `success`: 操作是否成功
- `message`: 操作结果消息

---

### 14. 实时吞吐量统计

**接口地址**: `GET /api/metrics/throughput/realtime`

**功能**: 获取实时吞吐量数据（最近5秒窗口）

**请求示例**:
```bash
curl http://localhost:8080/api/metrics/throughput/realtime
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "throughput": {
      "input_tokens_per_sec": 150.5,
      "output_tokens_per_sec": 45.2,
      "total_tokens_per_sec": 195.7,
      "cache_hit_tokens_per_sec": 80.3,
      "cache_miss_tokens_per_sec": 115.4
    }
  }
}
```

**返回字段说明**:
- `input_tokens_per_sec`: 输入token每秒处理量
- `output_tokens_per_sec`: 输出token每秒处理量
- `total_tokens_per_sec`: 总token每秒处理量
- `cache_hit_tokens_per_sec`: 缓存命中token每秒处理量
- `cache_miss_tokens_per_sec`: 缓存未命中token每秒处理量

---

### 15. 本次运行总消耗

**接口地址**: `GET /api/metrics/throughput/current-session`

**功能**: 获取本次程序运行的总消耗统计

**请求示例**:
```bash
curl http://localhost:8080/api/metrics/throughput/current-session
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "session_total": {
      "total_cost_yuan": 12.45,
      "total_input_tokens": 85000,
      "total_output_tokens": 25000,
      "total_cache_n": 30000,
      "total_prompt_n": 80000,
      "session_start_time": 1758800530.9786587
    }
  }
}
```

**返回字段说明**:
- `total_cost_yuan`: 本次运行总成本（元）
- `total_input_tokens`: 总输入token数
- `total_output_tokens`: 总输出token数
- `total_cache_n`: 总缓存命中token数
- `total_prompt_n`: 总未缓存token数
- `session_start_time`: 程序启动时间戳

---

### 16. Token分布比例

**接口地址**: `GET /api/analytics/token-distribution/{time_range}`

**功能**: 获取Token分布比例数据，支持不同时间范围的分析

**路径参数**:
- `time_range`: 时间范围 (`10min`, `30min`, `1h`, `12h`, `1d`, `1w`, `1m`, `3m`, `6m`, `1y`, `all`)

**请求示例**:
```bash
curl http://localhost:8080/api/analytics/token-distribution/1h
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "time_range": "1h",
    "time_points": [
      {
        "timestamp": 1758800014.797687,
        "data": {
          "Qwen3-8B-AWQ": 1500,
          "Qwen3-14B-AWQ": 800
        }
      }
    ],
    "model_token_data": {
      "Qwen3-8B-AWQ": 1500,
      "Qwen3-14B-AWQ": 800
    }
  }
}
```

**返回字段说明**:
- `time_range`: 查询的时间范围
- `time_points`: 时间点数据数组
  - `timestamp`: 时间戳
  - `data`: 各模型在该时间点的token数量
- `model_token_data`: 各模型在整个时间范围内的总token数

---

### 17. Token消耗趋势

**接口地址**: `GET /api/analytics/token-trends/{time_range}`

**功能**: 获取Token消耗趋势数据

**路径参数**:
- `time_range`: 时间范围 (`10min`, `30min`, `1h`, `12h`, `1d`, `1w`, `1m`, `3m`, `6m`, `1y`, `all`)

**请求示例**:
```bash
curl http://localhost:8080/api/analytics/token-trends/1h
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "time_range": "1h",
    "time_points": [
      {
        "timestamp": 1758800014.797687,
        "data": {
          "input_tokens": 800,
          "output_tokens": 400,
          "total_tokens": 1200,
          "cache_hit_tokens": 300,
          "cache_miss_tokens": 900
        }
      }
    ]
  }
}
```

**返回字段说明**:
- `time_range`: 查询的时间范围
- `time_points`: 时间点数据数组
  - `timestamp`: 时间戳
  - `data`: 该时间点的token统计数据
    - `input_tokens`: 输入token数
    - `output_tokens`: 输出token数
    - `total_tokens`: 总token数
    - `cache_hit_tokens`: 缓存命中token数
    - `cache_miss_tokens`: 缓存未命中token数

---

### 18. 成本趋势

**接口地址**: `GET /api/analytics/cost-trends/{time_range}`

**功能**: 获取成本趋势数据

**路径参数**:
- `time_range`: 时间范围 (`10min`, `30min`, `1h`, `12h`, `1d`, `1w`, `1m`, `3m`, `6m`, `1y`, `all`)

**请求示例**:
```bash
curl http://localhost:8080/api/analytics/cost-trends/1h
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "time_range": "1h",
    "time_points": [
      {
        "timestamp": 1758800014.797687,
        "cost": 0.025
      }
    ]
  }
}
```

**返回字段说明**:
- `time_range`: 查询的时间范围
- `time_points`: 时间点数据数组
  - `timestamp`: 时间戳
  - `cost`: 该时间段的成本（元）

---

### 19. 单模型统计数据

**接口地址**: `GET /api/analytics/model-stats/{model_name}/{time_range}`

**功能**: 获取指定模型的详细统计数据

**路径参数**:
- `model_name`: 模型名称
- `time_range`: 时间范围 (`10min`, `30min`, `1h`, `12h`, `1d`, `1w`, `1m`, `3m`, `6m`, `1y`, `all`)

**请求示例**:
```bash
curl http://localhost:8080/api/analytics/model-stats/Qwen3-8B-AWQ/1h
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "model_name": "Qwen3-8B-AWQ",
    "time_range": "1h",
    "summary": {
      "total_input_tokens": 8000,
      "total_output_tokens": 2000,
      "total_tokens": 10000,
      "total_cache_n": 3000,
      "total_prompt_n": 7000,
      "total_cost": 0.025,
      "request_count": 15
    },
    "time_points": [
      {
        "timestamp": 1758800014.797687,
        "data": {
          "input_tokens": 500,
          "output_tokens": 200,
          "total_tokens": 700,
          "cache_hit_tokens": 200,
          "cache_miss_tokens": 500,
          "cost": 0.002
        }
      }
    ]
  }
}
```

**返回字段说明**:
- `model_name`: 模型名称
- `time_range`: 查询的时间范围
- `summary`: 汇总统计
  - `total_input_tokens`: 总输入token数
  - `total_output_tokens`: 总输出token数
  - `total_tokens`: 总token数
  - `total_cache_n`: 总缓存命中token数
  - `total_prompt_n`: 总未缓存token数
  - `total_cost`: 总成本（元）
  - `request_count`: 请求总数
- `time_points`: 时间点数据数组

---

### 20. 获取模型计费配置

**接口地址**: `GET /api/billing/models/{model_name}/pricing`

**功能**: 获取指定模型的计费配置

**路径参数**:
- `model_name`: 模型名称

**请求示例**:
```bash
curl http://localhost:8080/api/billing/models/Qwen3-8B-AWQ/pricing
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "model_name": "Qwen3-8B-AWQ",
    "pricing_type": "tier",
    "tier_pricing": [
      {
        "tier_index": 1,
        "start_tokens": 0,
        "end_tokens": 32768,
        "input_price_per_million": 2.5,
        "output_price_per_million": 7.5,
        "support_cache": true,
        "cache_hit_price_per_million": 1.0
      }
    ],
    "hourly_price": 0.0
  }
}
```

**返回字段说明**:
- `model_name`: 模型名称
- `pricing_type`: 计费类型 (`tier`/`hourly`)
- `tier_pricing`: 阶梯计费配置（当pricing_type为"tier"时）
  - `tier_index`: 阶梯索引
  - `start_tokens`: 起始token数
  - `end_tokens`: 结束token数
  - `input_price_per_million`: 输入token价格（元/百万token）
  - `output_price_per_million`: 输出token价格（元/百万token）
  - `support_cache`: 是否支持缓存
  - `cache_hit_price_per_million`: 缓存命中价格（元/百万token）
- `hourly_price`: 每小时价格（当pricing_type为"hourly"时）

---

### 21. 设置模型阶梯计费

**接口地址**: `POST /api/billing/models/{model_name}/pricing/tier`

**功能**: 设置模型的阶梯计费配置

**路径参数**:
- `model_name`: 模型名称

**请求体**:
```json
{
  "tier_index": 1,
  "start_tokens": 0,
  "end_tokens": 32768,
  "input_price_per_million": 2.5,
  "output_price_per_million": 7.5,
  "support_cache": true,
  "cache_hit_price_per_million": 1.0
}
```

**请求示例**:
```bash
curl -X POST http://localhost:8080/api/billing/models/Qwen3-8B-AWQ/pricing/tier \
  -H "Content-Type: application/json" \
  -d '{
    "tier_index": 1,
    "start_tokens": 0,
    "end_tokens": 32768,
    "input_price_per_million": 2.5,
    "output_price_per_million": 7.5,
    "support_cache": true,
    "cache_hit_price_per_million": 1.0
  }'
```

**返回结构**:
```json
{
  "success": true,
  "message": "模型 'Qwen3-8B-AWQ' 阶梯计费配置已更新"
}
```

**请求字段说明**:
- `tier_index`: 阶梯索引
- `start_tokens`: 起始token数
- `end_tokens`: 结束token数
- `input_price_per_million`: 输入token价格（元/百万token）
- `output_price_per_million`: 输出token价格（元/百万token）
- `support_cache`: 是否支持缓存
- `cache_hit_price_per_million`: 缓存命中价格（元/百万token）

---

### 22. 设置模型按时计费

**接口地址**: `POST /api/billing/models/{model_name}/pricing/hourly`

**功能**: 设置模型的按时计费配置

**路径参数**:
- `model_name`: 模型名称

**请求体**:
```json
{
  "hourly_price": 0.5
}
```

**请求示例**:
```bash
curl -X POST http://localhost:8080/api/billing/models/Qwen3-8B-AWQ/pricing/hourly \
  -H "Content-Type: application/json" \
  -d '{"hourly_price": 0.5}'
```

**返回结构**:
```json
{
  "success": true,
  "message": "模型 'Qwen3-8B-AWQ' 按时计费配置已更新"
}
```

**请求字段说明**:
- `hourly_price`: 每小时价格（元）

---

### 23. 获取孤立模型数据

**接口地址**: `GET /api/data/models/orphaned`

**功能**: 获取配置中不存在但数据库中有数据的模型列表

**请求示例**:
```bash
curl http://localhost:8080/api/data/models/orphaned
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "orphaned_models": ["old-model-1", "old-model-2"],
    "count": 2
  }
}
```

**返回字段说明**:
- `orphaned_models`: 孤立模型名称列表
- `count`: 孤立模型数量

---

### 24. 删除模型数据

**接口地址**: `DELETE /api/data/models/{model_name}`

**功能**: 删除指定模型的所有数据（仅限不在配置中的模型）

**路径参数**:
- `model_name`: 模型名称

**请求示例**:
```bash
curl -X DELETE http://localhost:8080/api/data/models/old-model-1
```

**返回结构**:
```json
{
  "success": true,
  "message": "模型 'old-model-1' 的数据已删除"
}
```

**注意事项**:
- 只有不在配置中的模型才能被删除
- 删除操作不可逆，请谨慎操作

---

### 25. 获取存储统计

**接口地址**: `GET /api/data/storage/stats`

**功能**: 获取数据库存储统计信息

**请求示例**:
```bash
curl http://localhost:8080/api/data/storage/stats
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "database_exists": true,
    "database_size_mb": 0.3,
    "total_models_with_data": 1,
    "total_requests": 2,
    "models_data": {
      "Qwen3-8B-AWQ": {
        "request_count": 0,
        "has_runtime_data": true,
        "has_billing_data": true
      }
    }
  }
}
```

**返回字段说明**:
- `database_exists`: 数据库是否存在
- `database_size_mb`: 数据库文件大小（MB）
- `total_models_with_data`: 有数据的模型数量
- `total_requests`: 总请求数
- `models_data`: 各模型数据统计
  - `request_count`: 请求数量
  - `has_runtime_data`: 是否有运行时间数据
  - `has_billing_data`: 是否有计费数据

---

### 26. 统一API路由

**接口地址**: `/{path:path}`

**支持方法**: `GET`, `POST`, `PUT`, `DELETE`, `OPTIONS`, `HEAD`

**功能**: 处理所有OpenAI兼容的API请求，自动路由到对应模型

**支持的路径**:
- `/v1/chat/completions` - 聊天对话
- `/v1/completions` - 文本补全
- `/v1/embeddings` - 文本嵌入
- `/v1/rerank` - 重排序

#### 13.1 聊天对话

**接口地址**: `POST /v1/chat/completions`

**功能**: 聊天对话接口 (Chat模式模型)

**请求示例**:
```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-8B-AWQ",
    "messages": [
      {"role": "user", "content": "Hello, how are you?"}
    ],
    "max_tokens": 100,
    "temperature": 0.7,
    "stream": false
  }'
```

**请求参数**:
- `model`: 模型名称 (必需)
- `messages`: 消息列表 (必需)
- `max_tokens`: 最大生成长度 (可选)
- `temperature`: 温度参数 (可选)
- `stream`: 是否流式输出 (可选，默认false)

**返回结构**:
```json
{
  "id": "chatcmpl-123",
  "object": "chat.completion",
  "created": 1677652288,
  "model": "Qwen3-8B-AWQ",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Hello! I'm doing well, thank you for asking. How can I help you today?"
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 12,
    "completion_tokens": 20,
    "total_tokens": 32
  }
}
```

#### 13.2 文本补全

**接口地址**: `POST /v1/completions`

**功能**: 文本补全接口 (Base模式模型)

**请求示例**:
```bash
curl -X POST http://localhost:8080/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "base-model-name",
    "prompt": "Hello,",
    "max_tokens": 50,
    "temperature": 0.7
  }'
```

**请求参数**:
- `model`: 模型名称 (必需)
- `prompt`: 输入文本 (必需)
- `max_tokens`: 最大生成长度 (可选)
- `temperature`: 温度参数 (可选)

**返回结构**:
```json
{
  "id": "cmpl-123",
  "object": "text_completion",
  "created": 1677652288,
  "model": "base-model-name",
  "choices": [{
    "text": " world! How are you today?",
    "index": 0,
    "logprobs": null,
    "finish_reason": "length"
  }],
  "usage": {
    "prompt_tokens": 6,
    "completion_tokens": 44,
    "total_tokens": 50
  }
}
```

#### 13.3 文本嵌入

**接口地址**: `POST /v1/embeddings`

**功能**: 文本嵌入接口 (Embedding模式模型)

**请求示例**:
```bash
curl -X POST http://localhost:8080/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-Embedding-8B",
    "input": "Hello, world!",
    "encoding_format": "float"
  }'
```

**请求参数**:
- `model`: 模型名称 (必需)
- `input`: 输入文本 (必需)
- `encoding_format`: 编码格式 (可选，默认float)

**返回结构**:
```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "embedding": [0.1, 0.2, 0.3, ...],
      "index": 0
    }
  ],
  "model": "Qwen3-Embedding-8B",
  "usage": {
    "prompt_tokens": 4,
    "total_tokens": 4
  }
}
```

#### 13.4 重排序

**接口地址**: `POST /v1/rerank`

**功能**: 重排序接口 (Reranker模式模型)

**请求示例**:
```bash
curl -X POST http://localhost:8080/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{
    "model": "bge-reranker-v2-m3",
    "query": "What is artificial intelligence?",
    "documents": [
      "Artificial intelligence is a branch of computer science.",
      "Machine learning is a subset of AI.",
      "Deep learning uses neural networks."
    ],
    "top_n": 2
  }'
```

**请求参数**:
- `model`: 模型名称 (必需)
- `query`: 查询文本 (必需)
- `documents`: 文档列表 (必需)
- `top_n`: 返回top N结果 (可选)

**返回结构**:
```json
{
  "results": [
    {
      "index": 0,
      "document": "Artificial intelligence is a branch of computer science.",
      "relevance_score": 0.95
    },
    {
      "index": 1,
      "document": "Machine learning is a subset of AI.",
      "relevance_score": 0.87
    }
  ]
}
```

---

## 🚨 错误处理

### 错误响应格式

所有API接口在遇到错误时返回统一格式的错误响应：

```json
{
  "success": false,
  "error": "错误信息",
  "message": "详细错误描述"
}
```

### 常见错误码

- `400 Bad Request`: 请求参数错误
- `404 Not Found`: 资源不存在
- `500 Internal Server Error`: 服务器内部错误

### 错误类型

1. **模型未找到**: 模型别名不存在
2. **模型未启动**: 模型处于停止状态
3. **设备不可用**: 所需设备离线
4. **端口占用**: 端口被其他程序占用
5. **启动失败**: 模型启动过程出错

---

## 🔒 认证与授权

当前版本所有API接口无需认证，直接访问即可。

---

## 📊 性能说明

### 请求限制

- 并发请求数: 无硬性限制，受系统资源约束
- 请求超时: 默认300秒
- 响应大小: 无限制，受模型配置影响

### 资源管理

- **自动加载**: 请求时自动启动对应模型
- **智能卸载**: 空闲模型自动卸载释放资源
- **并发控制**: 每个模型独立处理并发请求

---

## 🔄 版本信息

- **当前版本**: v1.0.0
- **API兼容性**: 与OpenAI API完全兼容
- **更新日期**: 2025-09-22

---

## 📞 技术支持

> **重要声明**: 本项目为个人开发项目，**不提供任何技术支持**。请根据项目文档自行调试和修改代码。

如有问题，请：
1. 仔细阅读本文档
2. 检查配置文件和日志
3. 参考项目README.md
4. 自行调试解决

---

*最后更新: 2025-09-25*