# LLM-Manager Monitor 使用手册

## 概述

Monitor是LLM-Manager的核心监控组件，提供线程安全的数据库操作，用于跟踪模型运行状态、请求记录和计费管理。

## 快速开始

### 基本初始化

```python
from core.monitor import Monitor

# 创建监控器实例
monitor = Monitor()  # 默认使用 webui/monitoring.db
# 或指定数据库路径
monitor = Monitor("custom_monitoring.db")

# 使用完成后关闭
monitor.close()
```

### 线程安全

Monitor完全支持多进程/多线程并发访问，使用连接池管理数据库连接。

```python
# 在多线程环境中安全使用
def worker_thread():
    monitor = Monitor()
    monitor.add_model_request("model_name", [time.time(), 100, 50])
    monitor.close()

# 启动多个线程
threads = []
for i in range(5):
    thread = threading.Thread(target=worker_thread)
    threads.append(thread)
    thread.start()
```

## 核心功能

### 1. 模型名称安全化

#### `get_safe_model_name(model_name: str) -> str`
将原始模型名称转换为安全的表名（SHA256哈希）

```python
safe_name = monitor.get_safe_model_name("Qwen3-Coder-30B-A3B-Instruct-UD-64K")
# 返回: "model_13611871a86e4690"
```

#### `get_model_safe_name(model_name: str) -> Optional[str]`
根据原始名称获取已存储的安全名称

```python
safe_name = monitor.get_model_safe_name("Qwen3-Coder-30B-A3B-Instruct-64K")
# 返回: "model_c4f62ffe5da886b5" 或 None
```

### 2. 运行时间记录

#### `add_model_runtime_start(model_name: str, start_time: float)`
记录模型启动时间

```python
import time
start_time = time.time()
monitor.add_model_runtime_start("model_name", start_time)
```

#### `update_model_runtime_end(model_name: str, end_time: float)`
更新模型运行结束时间

```python
end_time = time.time()
monitor.update_model_runtime_end("model_name", end_time)
```

#### `add_program_runtime_start(start_time: float)`
记录程序启动时间

```python
monitor.add_program_runtime_start(time.time())
```

#### `update_program_runtime_end(end_time: float)`
更新程序运行结束时间

```python
monitor.update_program_runtime_end(time.time())
```

### 3. 请求记录

#### `add_model_request(model_name: str, request_data: List[Union[float, int, int]])`
添加模型请求记录

```python
# 格式: [时间戳, 输入token数, 输出token数]
request_data = [time.time(), 150, 75]
monitor.add_model_request("model_name", request_data)
```

### 4. 计费管理

#### `update_hourly_price(model_name: str, hourly_price: float)`
更新按时计费价格

```python
monitor.update_hourly_price("model_name", 12.5)  # $12.5/小时
```

#### `update_billing_method(model_name: str, use_tier_pricing: bool)`
更新计费方式

```python
# True = 按量计费, False = 按时计费
monitor.update_billing_method("model_name", True)
```

#### `add_tier_pricing(model_name: str, tier_data: List[Union[int, int, int, float, float]])`
添加计费阶梯

```python
# 格式: [阶梯索引, 起始token, 结束token, 输入价格/百万, 输出价格/百万]
tier_data = [2, 32769, 65536, 0.8, 2.0]
monitor.add_tier_pricing("model_name", tier_data)
```

#### `update_tier_pricing(model_name: str, tier_data: List[Union[int, int, int, float, float]])`
更新计费阶梯

```python
tier_data = [1, 0, 32768, 1.0, 2.5]
monitor.update_tier_pricing("model_name", tier_data)
```

#### `delete_tier_pricing(model_name: str, tier_index: int)`
删除计费阶梯

```python
monitor.delete_tier_pricing("model_name", 3)  # 删除阶梯3
```

### 5. 数据查询

#### `get_program_runtime(limit: int = 0) -> List[ModelRunTime]`
获取程序运行时间记录

```python
# 获取所有记录
all_runtime = monitor.get_program_runtime(0)

# 获取最近10条记录
recent_runtime = monitor.get_program_runtime(10)

# ModelRunTime 包含: id, start_time, end_time
for runtime in all_runtime:
    duration = runtime.end_time - runtime.start_time
    print(f"运行时长: {duration:.2f}秒")
```

#### `get_model_runtime(model_name: str, limit: int = 0) -> List[ModelRunTime]`
获取模型运行时间记录

```python
runtime_records = monitor.get_model_runtime("model_name", 5)
```

#### `get_model_requests(model_name: str, minutes: int = 0) -> List[ModelRequest]`
获取模型请求记录

```python
# 获取所有请求记录
all_requests = monitor.get_model_requests("model_name", 0)

# 获取最近30分钟的请求记录
recent_requests = monitor.get_model_requests("model_name", 30)

# ModelRequest 包含: id, timestamp, input_tokens, output_tokens
for request in recent_requests:
    total_tokens = request.input_tokens + request.output_tokens
    print(f"总tokens: {total_tokens}")
```

#### `get_model_billing(model_name: str) -> Optional[ModelBilling]`
获取模型计费配置

```python
billing = monitor.get_model_billing("model_name")
if billing:
    print(f"计费方式: {'按量计费' if billing.use_tier_pricing else '按时计费'}")
    print(f"每小时价格: ${billing.hourly_price}")

    for tier in billing.tier_pricing:
        print(f"阶梯 {tier.tier_index}: {tier.start_tokens}-{tier.end_tokens} tokens")
        print(f"  输入: ${tier.input_price_per_million}/M")
        print(f"  输出: ${tier.output_price_per_million}/M")
```

### 6. 数据删除

#### `delete_model_tables(model_name: str)`
删除模型相关的所有表和记录

```python
monitor.delete_model_tables("model_name")
# 将删除:
# - 模型的5个专用表
# - 名称映射表中的记录
```

## 数据结构

### ModelRunTime
```python
@dataclass
class ModelRunTime:
    id: int           # 记录ID
    start_time: float # 启动时间戳
    end_time: float   # 结束时间戳
```

### ModelRequest
```python
@dataclass
class ModelRequest:
    id: int           # 记录ID
    timestamp: float  # 请求时间戳
    input_tokens: int # 输入token数
    output_tokens: int # 输出token数
```

### TierPricing
```python
@dataclass
class TierPricing:
    tier_index: int                # 阶梯索引
    start_tokens: int              # 起始token数
    end_tokens: int                # 结束token数
    input_price_per_million: float # 输入价格/百万token
    output_price_per_million: float # 输出价格/百万token
```

### ModelBilling
```python
@dataclass
class ModelBilling:
    use_tier_pricing: bool   # True=按量计费, False=按时计费
    hourly_price: float      # 每小时价格
    tier_pricing: List[TierPricing] # 阶梯价格列表
```

## 数据库结构

Monitor自动创建以下表结构：

### 核心表
- `model_name_mapping` - 模型名称映射表
- `program_runtime` - 程序运行时间表

### 每个模型的专用表
- `{safe_name}_runtime` - 模型运行时间表
- `{safe_name}_requests` - 模型请求记录表
- `{safe_name}_tier_pricing` - 按量分阶计费表
- `{safe_name}_hourly_price` - 按时计费价格表
- `{safe_name}_billing_method` - 计费方式选择表

## 使用示例

### 完整的使用流程

```python
import time
from core.monitor import Monitor

# 1. 初始化监控器
monitor = Monitor("my_monitoring.db")

try:
    # 2. 记录模型启动
    model_name = "Qwen3-Coder-30B-A3B-Instruct-64K"
    start_time = time.time()
    monitor.add_model_runtime_start(model_name, start_time)

    # 3. 设置计费方式
    monitor.update_billing_method(model_name, True)  # 按量计费

    # 4. 设置阶梯价格
    monitor.update_tier_pricing(model_name, [1, 0, 32768, 1.0, 2.5])
    monitor.add_tier_pricing(model_name, [2, 32769, 65536, 0.8, 2.0])

    # 5. 模拟处理请求
    for i in range(3):
        request_data = [time.time(), 100 + i*50, 50 + i*25]
        monitor.add_model_request(model_name, request_data)
        time.sleep(1)

    # 6. 记录模型停止
    end_time = time.time()
    monitor.update_model_runtime_end(model_name, end_time)

    # 7. 查看统计数据
    runtime_records = monitor.get_model_runtime(model_name, 1)
    request_records = monitor.get_model_requests(model_name, 5)
    billing_config = monitor.get_model_billing(model_name)

    print(f"运行时长: {runtime_records[0].end_time - runtime_records[0].start_time:.2f}秒")
    print(f"请求数量: {len(request_records)}")
    print(f"计费方式: {'按量计费' if billing_config.use_tier_pricing else '按时计费'}")

finally:
    # 8. 关闭监控器
    monitor.close()
```

### 批量操作示例

```python
def process_model_batch(model_names, requests_data):
    monitor = Monitor()

    try:
        # 批量启动模型
        for model_name in model_names:
            monitor.add_model_runtime_start(model_name, time.time())

        # 批量处理请求
        for model_name, request_data in requests_data:
            monitor.add_model_request(model_name, request_data)

        # 批量停止模型
        for model_name in model_names:
            monitor.update_model_runtime_end(model_name, time.time())

    finally:
        monitor.close()

# 使用示例
models = ["model1", "model2", "model3"]
requests = [
    ("model1", [time.time(), 100, 50]),
    ("model2", [time.time(), 200, 100]),
    ("model3", [time.time(), 150, 75])
]
process_model_batch(models, requests)
```

## 最佳实践

### 1. 资源管理
- 使用`try-finally`确保监控器正确关闭
- 在长期运行的应用中定期创建新的监控器实例

### 2. 并发访问
- Monitor完全线程安全，可在多线程环境中使用
- 每个线程建议创建独立的Monitor实例

### 3. 错误处理
- 捕获并处理可能的异常
- 验证模型名称是否存在

```python
try:
    safe_name = monitor.get_model_safe_name(model_name)
    if safe_name is None:
        raise ValueError(f"模型 {model_name} 不存在")

    monitor.add_model_request(model_name, request_data)
except ValueError as e:
    print(f"错误: {e}")
except Exception as e:
    print(f"数据库错误: {e}")
```

### 4. 性能考虑
- 批量操作时考虑使用事务
- 大量数据写入时适当增加连接池大小

## 注意事项

1. **数据库文件**: 默认在`webui/monitoring.db`，确保目录存在且有写权限
2. **表名安全**: 所有表名都经过SHA256哈希处理，确保安全性
3. **并发安全**: 使用连接池管理，支持高并发访问
4. **数据一致性**: 所有操作都是原子的，保证数据一致性
5. **资源清理**: 使用完成后务必调用`close()`方法释放资源

## 版本历史

- v1.0.0: 初始版本，支持完整的监控功能
- 线程安全的数据库操作
- 支持模型运行时间、请求记录、计费管理
- 完整的错误处理和资源管理