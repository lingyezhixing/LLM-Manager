# LLM-Manager DataManager 使用手册

## 概述

DataManager是LLM-Manager的核心数据管理组件，提供线程安全的数据库操作，负责模型运行监控、请求记录、计费管理和数据持久化存储。

## 主要功能

### 🗄️ 数据库管理
- SQLite数据库连接池管理
- 线程安全的并发访问
- 自动表结构创建和维护
- 数据备份和恢复

### 📊 运行时监控
- 模型启动/停止时间记录
- 程序运行时间统计
- 模型状态追踪
- 性能数据收集

### 💰 计费管理
- 阶梯计费配置
- 灵活的价格策略
- Token使用量统计
- 成本分析

### 📈 数据分析
- 使用量统计和分析
- 模型性能指标
- 历史数据查询
- 报表生成

## 快速开始

### 基本初始化

```python
from core.data_manager import DataManager

# 创建数据管理器实例
data_manager = DataManager()  # 默认使用 webui/monitoring.db
# 或指定数据库路径
data_manager = DataManager("custom_data.db")

# 使用完成后关闭
data_manager.close()
```

### 线程安全

DataManager完全支持多进程/多线程并发访问，使用连接池管理数据库连接。

```python
# 在多线程环境中安全使用
def worker_thread():
    data_manager = DataManager()
    # 执行数据操作
    data_manager.add_model_runtime_start("model_name", time.time())
    data_manager.close()
```

## 核心功能详解

### 1. 运行时管理

#### 模型运行时间记录

```python
import time

# 记录模型启动
data_manager.add_model_runtime_start("Qwen3-Coder-30B", time.time())

# 模型运行中...

# 记录模型停止
data_manager.update_model_runtime_end("Qwen3-Coder-30B", time.time())
```

#### 程序运行时间记录

```python
# 记录程序启动
data_manager.add_program_runtime_start(time.time())

# 程序运行中...

# 定期更新程序运行结束时间（用于存活时间统计）
data_manager.update_program_runtime_end(time.time())
```

#### 查询运行时间数据

```python
# 获取程序运行历史
runtime_history = data_manager.get_program_runtime(limit=10)
for record in runtime_history:
    print(f"运行时长: {record.end_time - record.start_time:.2f}秒")

# 获取特定模型的运行历史
model_runtime = data_manager.get_model_runtime("Qwen3-Coder-30B", limit=5)
for record in model_runtime:
    print(f"运行时长: {record.end_time - record.start_time:.2f}秒")
```

### 2. 请求记录管理

#### 记录模型请求

```python
import time

# 记录一次模型请求
request_data = [
    time.time(),           # 时间戳
    150,                  # 输入token数
    80,                   # 输出token数
    5,                    # cache命中数
    25                    # prompt数
]

data_manager.add_model_request("Qwen3-Coder-30B", request_data)
```

#### 查询请求历史

```python
# 获取最近1小时的请求记录
recent_requests = data_manager.get_model_requests("Qwen3-Coder-30B", minutes=60)

total_input = sum(req.input_tokens for req in recent_requests)
total_output = sum(req.output_tokens for req in recent_requests)

print(f"最近1小时总token使用: 输入{total_input}, 输出{total_output}")
```

### 3. 计费管理

#### 阶梯计费配置

```python
# 添加阶梯计费规则
tier_data = [
    1,                    # 阶梯索引
    0,                    # 起始token数
    1000000,              # 结束token数
    0.002,                # 输入价格(每百万token)
    0.008,                # 输出价格(每百万token)
    True,                 # 支持缓存
    0.001                 # 缓存命中价格(每百万token)
]

data_manager.add_tier_pricing("Qwen3-Coder-30B", tier_data)

# 更新阶梯计费规则
updated_tier_data = [
    1, 0, 2000000, 0.0015, 0.006, True, 0.0008
]
data_manager.update_tier_pricing("Qwen3-Coder-30B", updated_tier_data)
```

#### 简单计费配置

```python
# 设置按小时计费
data_manager.update_hourly_price("Qwen3-Coder-30B", 0.5)  # 每小时0.5元

# 切换计费模式
data_manager.update_billing_method("Qwen3-Coder-30B", use_tier_pricing=False)
```

#### 查询计费配置

```python
# 获取模型计费信息
billing_info = data_manager.get_model_billing("Qwen3-Coder-30B")
if billing_info:
    print(f"计费模式: {'阶梯计费' if billing_info.use_tier_pricing else '按时计费'}")
    if billing_info.use_tier_pricing:
        for tier in billing_info.tier_pricing:
            print(f"阶梯{tier.tier_index}: {tier.start_tokens}-{tier.end_tokens} tokens")
    else:
        print(f"每小时价格: {billing_info.hourly_price}元")
```

### 4. 数据管理

#### 模型名称安全管理

```python
# 获取模型的安全名称（用于数据库表名）
safe_name = data_manager.get_safe_model_name("Qwen3-Coder-30B-A3B-Instruct-UD-64K")
print(f"安全名称: {safe_name}")

# 通过安全名称获取原始名称
original_name = data_manager.get_model_safe_name(safe_name)
print(f"原始名称: {original_name}")
```

#### 数据清理

```python
# 删除模型相关所有数据（谨慎使用）
data_manager.delete_model_tables("Qwen3-Coder-30B")
```

## 高级用法

### 数据库连接池配置

```python
# 自定义连接池大小
data_manager = DataManager("custom.db")
# 连接池在DataManager内部自动管理
```

### 批量操作

```python
# 批量记录请求
import time

requests_batch = []
for i in range(10):
    request_data = [
        time.time() + i,      # 时间戳
        100 + i * 10,        # 输入token
        50 + i * 5,          # 输出token
        i,                   # cache命中数
        i * 2                # prompt数
    ]
    requests_batch.append(("Qwen3-Coder-30B", request_data))

# 批量添加
for model_name, req_data in requests_batch:
    data_manager.add_model_request(model_name, req_data)
```

### 数据分析示例

```python
def analyze_model_usage(data_manager, model_name, days=7):
    """分析模型使用情况"""
    import datetime

    # 获取指定天数的数据
    minutes = days * 24 * 60
    requests = data_manager.get_model_requests(model_name, minutes)

    if not requests:
        return "无使用数据"

    # 统计分析
    total_requests = len(requests)
    total_input_tokens = sum(req.input_tokens for req in requests)
    total_output_tokens = sum(req.output_tokens for req in requests)
    avg_input_tokens = total_input_tokens / total_requests
    avg_output_tokens = total_output_tokens / total_requests

    # 获取计费信息
    billing = data_manager.get_model_billing(model_name)

    analysis = {
        "model": model_name,
        "period_days": days,
        "total_requests": total_requests,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "avg_input_tokens": round(avg_input_tokens, 2),
        "avg_output_tokens": round(avg_output_tokens, 2),
        "billing_info": billing
    }

    return analysis

# 使用示例
analysis = analyze_model_usage(data_manager, "Qwen3-Coder-30B", days=7)
print(f"模型使用分析: {analysis}")
```

## 错误处理

### 基本错误处理

```python
try:
    data_manager = DataManager()

    # 记录模型运行时间
    data_manager.add_model_runtime_start("invalid_model", time.time())

except Exception as e:
    print(f"数据操作失败: {e}")
    # 错误处理逻辑
finally:
    data_manager.close()
```

### 模型不存在处理

```python
model_name = "nonexistent_model"

# 检查模型是否存在
safe_name = data_manager.get_model_safe_name(model_name)
if not safe_name:
    print(f"模型 {model_name} 不存在")
else:
    # 执行操作
    runtime = data_manager.get_model_runtime(model_name)
```

## 性能优化

### 连接池管理

```python
# DataManager自动管理连接池，无需手动操作
# 连接池大小在初始化时确定，支持并发访问
```

### 批量操作优化

```python
# 对于大量数据插入，建议批量处理
def batch_add_requests(data_manager, model_name, requests_data):
    """批量添加请求记录"""
    for request_data in requests_data:
        try:
            data_manager.add_model_request(model_name, request_data)
        except Exception as e:
            logger.error(f"添加请求失败: {e}")
            continue
```

## 数据库结构

### 核心表结构

```sql
-- 模型名称映射表
CREATE TABLE model_name_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_name TEXT UNIQUE NOT NULL,
    safe_name TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 程序运行时间表
CREATE TABLE program_runtime (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL
);

-- 模型运行时间表（每个模型一个表）
CREATE TABLE {safe_name}_runtime (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL
);

-- 模型请求记录表（每个模型一个表）
CREATE TABLE {safe_name}_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_n INTEGER NOT NULL,
    prompt_n INTEGER NOT NULL
);

-- 模型计费配置表（每个模型一个表）
CREATE TABLE {safe_name}_billing (
    use_tier_pricing BOOLEAN NOT NULL,
    hourly_price REAL
);

-- 阶梯计费配置表（每个模型一个表）
CREATE TABLE {safe_name}_tier_pricing (
    tier_index INTEGER PRIMARY KEY,
    start_tokens INTEGER NOT NULL,
    end_tokens INTEGER NOT NULL,
    input_price_per_million REAL NOT NULL,
    output_price_per_million REAL NOT NULL,
    support_cache BOOLEAN NOT NULL,
    cache_hit_price_per_million REAL
);
```

## 最佳实践

### 1. 生命周期管理

```python
# 推荐使用上下文管理器
class DataManagerContext:
    def __init__(self, db_path=None):
        self.db_path = db_path
        self.data_manager = None

    def __enter__(self):
        self.data_manager = DataManager(self.db_path)
        return self.data_manager

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.data_manager:
            self.data_manager.close()

# 使用示例
with DataManagerContext() as dm:
    dm.add_model_runtime_start("model_name", time.time())
    # 其他操作...
```

### 2. 异常处理

```python
def safe_data_operation(data_manager, operation, *args, **kwargs):
    """安全的数据操作包装器"""
    try:
        return operation(data_manager, *args, **kwargs), None
    except Exception as e:
        logger.error(f"数据操作失败: {e}")
        return None, str(e)

# 使用示例
result, error = safe_data_operation(
    data_manager.add_model_request,
    "model_name",
    request_data
)
if error:
    print(f"操作失败: {error}")
```

### 3. 定期维护

```python
def database_maintenance(data_manager):
    """数据库维护操作"""
    try:
        # 检查数据库完整性
        # 清理过期数据（根据业务需求）
        # 更新统计信息

        logger.info("数据库维护完成")
    except Exception as e:
        logger.error(f"数据库维护失败: {e}")
```

## 常见问题

### Q: 如何处理数据库锁定？

A: DataManager使用连接池自动处理并发访问，一般情况下不会出现锁定问题。如果遇到锁定，请检查是否有未关闭的连接。

### Q: 数据库文件在哪里？

A: 默认位置是 `webui/monitoring.db`，可以在初始化时指定自定义路径。

### Q: 如何备份数据？

A: 直接复制数据库文件即可，SQLite支持热备份。

### Q: 支持哪些数据类型？

A: 支持基本的SQLite数据类型：INTEGER, REAL, TEXT, BLOB

---

*文档生成时间: 2024-09-25*
*版本: 1.0.0*