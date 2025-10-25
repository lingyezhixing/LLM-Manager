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

**接口地址**: `POST /api/logs/{model_alias}/clear/{keep_minutes}`

**功能**: 清理模型控制台日志，支持选择性保留

**路径参数**:
- `model_alias`: 模型别名
- `keep_minutes`: (可选, 默认0) 保留最近多少分钟的日志，0表示清空所有

**请求示例**:
```bash
# 清空所有日志
curl -X POST http://localhost:8080/api/logs/Qwen3-8B-AWQ/clear/0

# 保留最近10分钟的日志
curl -X POST http://localhost:8080/api/logs/Qwen3-8B-AWQ/clear/10
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

### 14. 吞吐量趋势分析

**接口地址**: `GET /api/metrics/throughput/{start_time}/{end_time}/{n_samples}`

**功能**: 获取指定时间段内，所有模型合并计算的吞吐量趋势数据，并按模型模式细分。

**路径参数**:
- `start_time`: 开始时间戳 (Unix Timestamp, float)
- `end_time`: 结束时间戳 (Unix Timestamp, float)
- `n_samples`: 采样点数量 (integer)，API 会将指定时间段均匀划分为 `n_samples` 个区间进行统计。

**请求示例**:
```bash
# 获取从 1758820000 到 1758822000 时间段内，分为10个采样点的吞吐量数据
curl http://localhost:8080/api/metrics/throughput/1758820000/1758822000/10
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "time_points": [
      {
        "timestamp": 1758820200.0,
        "data": {
          "input_tokens_per_sec": 150.5,
          "output_tokens_per_sec": 45.2,
          "total_tokens_per_sec": 195.7,
          "cache_hit_tokens_per_sec": 80.3,
          "cache_miss_tokens_per_sec": 70.2
        }
      }
      // ... more data points (总共n_samples个)
    ],
    "mode_breakdown": {
      "Chat": [
        {
          "timestamp": 1758820200.0,
          "data": {
            "input_tokens_per_sec": 120.0,
            "output_tokens_per_sec": 40.0,
            "total_tokens_per_sec": 160.0,
            "cache_hit_tokens_per_sec": 70.0,
            "cache_miss_tokens_per_sec": 50.0
          }
        }
        // ... Chat模式的n_samples个数据点
      ],
      "Embedding": [
        {
          "timestamp": 1758820200.0,
          "data": {
            "input_tokens_per_sec": 30.5,
            "output_tokens_per_sec": 5.2,
            "total_tokens_per_sec": 35.7,
            "cache_hit_tokens_per_sec": 10.3,
            "cache_miss_tokens_per_sec": 20.2
          }
        }
        // ... Embedding模式的n_samples个数据点
      ]
      // ... 其他模式如 Base, Reranker 的数据
    }
  }
}
```

**返回字段说明**:
- `time_points`: **总体**时间点数据数组，长度等于 `n_samples`
  - `timestamp`: 每个采样区间的**结束时间戳**
  - `data`: 该时间区间的吞吐量统计
    - `input_tokens_per_sec`: 输入token每秒处理量
    - `output_tokens_per_sec`: 输出token每秒处理量
    - `total_tokens_per_sec`: 总token每秒处理量
    - `cache_hit_tokens_per_sec`: 缓存命中token每秒处理量
    - `cache_miss_tokens_per_sec`: 缓存未命中token每秒处理量
- `mode_breakdown`: 一个字典，键为模型模式 (e.g., "Chat", "Embedding")，值为该模式下的时间点数据数组，结构与 `time_points` 相同。

---

### 15. 本次运行总消耗

**接口地址**: `GET /api/metrics/throughput/current-session`

**功能**: 获取本次程序运行的总消耗统计。

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
      "total_prompt_n": 55000,
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
- `total_prompt_n`: 总缓存未命中token数
- `session_start_time`: 程序启动时间戳

---

### 16. 使用量汇总

**接口地址**: `GET /api/analytics/usage-summary/{start_time}/{end_time}`

**功能**: 获取在指定时间范围内，按模型模式分类的**Token总消耗**和**资金总成本**。

**路径参数**:
-   `start_time`: 开始时间戳 (Unix Timestamp, float)
-   `end_time`: 结束时间戳 (Unix Timestamp, float)

**请求示例**:
```bash
curl http://localhost:8080/api/analytics/usage-summary/1758820000/1758822000
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "mode_summary": {
      "Chat": {
        "total_tokens": 15000,
        "total_cost": 0.025
      },
      "Embedding": {
        "total_tokens": 8000,
        "total_cost": 0.001
      },
      "Image": {
        "total_tokens": 0,
        "total_cost": 0.0
      }
    },
    "overall_summary": {
      "total_tokens": 23000,
      "total_cost": 0.026
    }
  }
}
```

**返回字段说明**:
-   `mode_summary`: 一个字典，提供了按模型模式细分的消耗数据。
    -   **键**: 模型模式名称 (例如 "Chat")。
    -   **值**: 一个包含该模式总消耗的对象。
        -   `total_tokens`: 该模式消耗的Token总数。
        -   `total_cost`: 该模式产生的资金成本总额。
-   `overall_summary`: 一个对象，提供了所有模式的总消耗数据。
    -   `total_tokens`: 所有模式消耗的Token总数。
    -   `total_cost`: 所有模式产生的资金成本总额。

---

### 17. Token消耗趋势

**接口地址**: `GET /api/analytics/token-trends/{start_time}/{end_time}/{n_samples}`

**功能**: 获取在指定时间范围内，所有模型合并计算的Token消耗总量趋势，并按模型模式细分。

**路径参数**:
- `start_time`: 开始时间戳 (Unix Timestamp, float)
- `end_time`: 结束时间戳 (Unix Timestamp, float)
- `n_samples`: 采样点数量 (integer)

**请求示例**:
```bash
curl http://localhost:8080/api/analytics/token-trends/1758820000/1758822000/10
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "time_points": [
      {
        "timestamp": 1758820200.0,
        "data": {
          "input_tokens": 800,
          "output_tokens": 400,
          "total_tokens": 1200,
          "cache_hit_tokens": 300,
          "cache_miss_tokens": 500
        }
      }
      // ... more data points
    ],
    "mode_breakdown": {
      "Chat": [
        {
          "timestamp": 1758820200.0,
          "data": {
            "input_tokens": 600,
            "output_tokens": 300,
            "total_tokens": 900,
            "cache_hit_tokens": 250,
            "cache_miss_tokens": 350
          }
        }
        // ... Chat模式的n_samples个数据点
      ],
      "Embedding": [
        {
          "timestamp": 1758820200.0,
          "data": {
            "input_tokens": 200,
            "output_tokens": 100,
            "total_tokens": 300,
            "cache_hit_tokens": 50,
            "cache_miss_tokens": 150
          }
        }
        // ... Embedding模式的n_samples个数据点
      ]
    }
  }
}
```

**返回字段说明**:
- `time_points`: **总体**时间点数据数组，长度等于 `n_samples`
  - `timestamp`: 每个采样区间的**结束时间戳**
  - `data`: 该时间区间的Token消耗总量
    - `input_tokens`: 输入token数
    - `output_tokens`: 输出token数
    - `total_tokens`: 总token数
    - `cache_hit_tokens`: 缓存命中token数
    - `cache_miss_tokens`: 缓存未命中token数
- `mode_breakdown`: 一个字典，键为模型模式，值为该模式下的时间点数据数组，结构与 `time_points` 相同。

---

### 18. 成本趋势

**接口地址**: `GET /api/analytics/cost-trends/{start_time}/{end_time}/{n_samples}`

**功能**: 获取在指定时间范围内，所有模型合并计算的成本趋势，并按模型模式细分。

**路径参数**:
- `start_time`: 开始时间戳 (Unix Timestamp, float)
- `end_time`: 结束时间戳 (Unix Timestamp, float)
- `n_samples`: 采样点数量 (integer)

**请求示例**:
```bash
curl http://localhost:8080/api/analytics/cost-trends/1758820000/1758822000/10
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "time_points": [
      {
        "timestamp": 1758820200.0,
        "data": {
          "cost": 0.025
        }
      }
      // ... more data points
    ],
    "mode_breakdown": {
      "Chat": [
        {
          "timestamp": 1758820200.0,
          "data": {
            "cost": 0.020
          }
        }
        // ... Chat模式的n_samples个数据点
      ],
      "Embedding": [
        {
          "timestamp": 1758820200.0,
          "data": {
            "cost": 0.005
          }
        }
        // ... Embedding模式的n_samples个数据点
      ]
    }
  }
}
```

**返回字段说明**:
- `time_points`: **总体**时间点数据数组，长度等于 `n_samples`
  - `timestamp`: 每个采样区间的**结束时间戳**
  - `data`:
    - `cost`: 该时间区间的总成本（元）
- `mode_breakdown`: 一个字典，键为模型模式，值为该模式下的时间点数据数组，结构与 `time_points` 相同。

---

### 19. 单模型统计数据

**接口地址**: `GET /api/analytics/model-stats/{model_name_alias}/{start_time}/{end_time}/{n_samples}`

**功能**: 获取指定模型在特定时间范围内的详细统计数据，包括总体概览和分时趋势。

**路径参数**:
- `model_name_alias`: 模型别名
- `start_time`: 开始时间戳 (Unix Timestamp, float)
- `end_time`: 结束时间戳 (Unix Timestamp, float)
- `n_samples`: 采样点数量 (integer)

**请求示例**:
```bash
curl http://localhost:8080/api/analytics/model-stats/Qwen3-8B-AWQ/1758820000/1758822000/10
```

**返回结构**:
```json
{
  "success": true,
  "data": {
    "model_name": "Qwen3-8B-AWQ",
    "summary": {
      "total_input_tokens": 8000,
      "total_output_tokens": 2000,
      "total_tokens": 10000,
      "total_cache_n": 3000,
      "total_prompt_n": 5000,
      "total_cost": 0.025,
      "request_count": 15
    },
    "time_points": [
      {
        "timestamp": 1758820200.0,
        "data": {
          "input_tokens": 500,
          "output_tokens": 200,
          "total_tokens": 700,
          "cache_hit_tokens": 200,
          "cache_miss_tokens": 300,
          "cost": 0.002
        }
      }
      // ... more data points
    ]
  }
}
```

**返回字段说明**:
- `model_name`: 模型名称
- `summary`: 在整个时间范围内的汇总统计
  - `total_input_tokens`: 总输入token数
  - `total_output_tokens`: 总输出token数
  - `total_tokens`: 总token数
  - `total_cache_n`: 总缓存命中token数
  - `total_prompt_n`: 总缓存未命中token数
  - `total_cost`: 总成本（元）
  - `request_count`: 请求总数
- `time_points`: 时间点数据数组，长度等于 `n_samples`
  - `timestamp`: 每个采样区间的**结束时间戳**
  - `data`: 该时间区间的统计数据
    - `input_tokens`: 该区间的输入token数
    - `output_tokens`: 该区间的输出token数
    - `total_tokens`: 该区间的总token数
    - `cache_hit_tokens`: 该区间的缓存命中token数
    - `cache_miss_tokens`: 该区间的缓存未命中token数
    - `cost`: 该区间的成本（元）

---

### **20. 获取模型计费配置**

**接口地址**: `GET /api/billing/models/{model_name}/pricing`

**功能**: 获取指定模型的计费配置，包括按时计费和新的分阶按量计费档位。

**路径参数**:
*   `model_name`: 模型名称或别名。

**请求示例**:
```bash
curl http://localhost:8080/api/billing/models/Qwen-VL-Chat/pricing
```

**返回结构**:```json
{
  "success": true,
  "data": {
    "model_name": "Qwen-VL-Chat",
    "pricing_type": "tier",
    "tier_pricing": [
      {
        "tier_index": 1,
        "min_input_tokens": 0,
        "max_input_tokens": 4096,
        "min_output_tokens": 0,
        "max_output_tokens": 4096,
        "input_price": 5.0,
        "output_price": 10.0,
        "support_cache": true,
        "cache_write_price": 0.5,
        "cache_read_price": 0.2
      },
      {
        "tier_index": 2,
        "min_input_tokens": 4096,
        "max_input_tokens": -1,
        "min_output_tokens": 0,
        "max_output_tokens": -1,
        "input_price": 8.0,
        "output_price": 15.0,
        "support_cache": false,
        "cache_write_price": 0.0,
        "cache_read_price": 0.0
      }
    ],
    "hourly_price": 0.0
  }
}```

**返回字段说明**:
*   `model_name`: 模型的唯一主名称。
*   `pricing_type`: 当前生效的计费类型 (`tier` - 按量 / `hourly` - 按时)。
*   `tier_pricing`: 分阶按量计费的档位配置列表（当 `pricing_type` 为 "tier" 时）。
    *   `tier_index`: 档位索引，唯一标识。
    *   `min_input_tokens`: 匹配此档位的最小输入 Token 数（**不包含**此值，即 `> min`）。
    *   `max_input_tokens`: 匹配此档位的最大输入 Token 数（**包含**此值，即 `<= max`）。`-1` 表示无上限。
    *   `min_output_tokens`: 匹配此档位的最小输出 Token 数（**不包含**此值）。
    *   `max_output_tokens`: 匹配此档位的最大输出 Token 数（**包含**此值）。`-1` 表示无上限。
    *   `input_price`: 在此档位下，缓外输入 Token 的价格（元/百万 Token）。
    *   `output_price`: 在此档位下，输出 Token 的价格（元/百万 Token）。
    *   `support_cache`: 在此档位下是否启用缓存计费。
    *   `cache_write_price`: 在此档位下，缓存写入的价格（元/百万 Token）。
    *   `cache_read_price`: 在此档位下，缓存读取的价格（元/百万 Token）。
*   `hourly_price`: 每小时的使用价格（元），仅在 `pricing_type` 为 "hourly" 时作为计费依据。

---

### **21. 设置模型分阶按量计费**

**接口地址**: `POST /api/billing/models/{model_name}/pricing/tier`

**功能**: 新增或更新一个计费档位。调用此接口会自动将模型的计费方式切换为“按量计费”。

**路径参数**:
*   `model_name`: 模型名称或别名。

**请求体 (JSON)**:
```json
{
  "tier_index": 1,
  "min_input_tokens": 0,
  "max_input_tokens": 4096,
  "min_output_tokens": 0,
  "max_output_tokens": 4096,
  "input_price": 5.0,
  "output_price": 10.0,
  "support_cache": true,
  "cache_write_price": 0.5,
  "cache_read_price": 0.2
}
```

**请求示例**:
```bash
curl -X POST http://localhost:8080/api/billing/models/Qwen-VL-Chat/pricing/tier \
  -H "Content-Type: application/json" \
  -d '{
    "tier_index": 1,
    "min_input_tokens": 0,
    "max_input_tokens": 4096,
    "min_output_tokens": 0,
    "max_output_tokens": 4096,
    "input_price": 5.0,
    "output_price": 10.0,
    "support_cache": true,
    "cache_write_price": 0.5,
    "cache_read_price": 0.2
  }'
```

**返回结构**:
```json
{
  "success": true,
  "message": "模型 'Qwen-VL-Chat' 的按量计费档位配置已更新"
}
```

**请求字段说明 (全部必填)**:
*   `tier_index`: **档位索引**。如果该索引已存在，则**更新**；如果不存在，则**新增**。
*   `min_input_tokens`: 最小输入 Token 数（不含）。
*   `max_input_tokens`: 最大输入 Token 数（包含）。使用 `-1` 表示无穷大。
*   `min_output_tokens`: 最小输出 Token 数（不含）。
*   `max_output_tokens`: 最大输出 Token 数（包含）。使用 `-1` 表示无穷大。
*   `input_price`: 缓外输入价格（元/百万 Token）。
*   `output_price`: 输出价格（元/百万 Token）。
*   `support_cache`: `true` 或 `false`，决定此档位是否应用缓存计费规则。
*   `cache_write_price`: 缓存写入价格（元/百万 Token）。如果 `support_cache` 为 `false`，此值虽必填但不会被使用。
*   `cache_read_price`: 缓存读取价格（元/百万 Token）。如果 `support_cache` 为 `false`，此值虽必填但不会被使用。

---

### **22. 设置模型按时计费**

**接口地址**: `POST /api/billing/models/{model_name}/pricing/hourly`

**功能**: 设置模型的按时计费价格。调用此接口会自动将模型的计费方式切换为“按时计费”。

**路径参数**:
*   `model_name`: 模型名称或别名。

**请求体 (JSON)**:
```json
{
  "hourly_price": 0.5
}
```

**请求示例**:```bash
curl -X POST http://localhost:8080/api/billing/models/Qwen-VL-Chat/pricing/hourly \
  -H "Content-Type: application/json" \
  -d '{"hourly_price": 0.5}'
```

**返回结构**:
```json
{
  "success": true,
  "message": "模型 'Qwen-VL-Chat' 按时计费配置已更新"
}
```

**请求字段说明**:
*   `hourly_price`: 每小时的价格（元）。

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