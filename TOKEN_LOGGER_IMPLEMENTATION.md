# Token记录功能实现总结

我已经成功在OpenAI API路由器中实现了一个异步的非侵入式请求token记录器。

## 主要功能

### 1. 监控器初始化
- 在`APIServer`类中初始化了监控器实例
- 监控器会自动为每个模型创建相应的数据库表来存储请求记录

### 2. Token提取功能
- 实现了从OpenAI API响应中自动提取token信息
- 支持`usage`字段中的`prompt_tokens`和`completion_tokens`
- 对缺失或无效的token信息有容错处理

### 3. 异步记录机制
- 使用`asyncio.to_thread`实现异步数据库写入
- 不会阻塞主要请求处理流程
- 对记录失败有错误处理，不影响用户请求

### 4. 非侵入式设计
- 不影响现有的路由处理逻辑
- 不影响请求的响应时间和流畅性
- 在请求完成后才进行token记录

## 实现的关键组件

### 1. Token提取方法
```python
def extract_tokens_from_response(self, response_content: bytes) -> tuple[int, int]:
    """从响应内容中提取token信息"""
    try:
        data = json.loads(response_content.decode('utf-8'))
        if "usage" in data:
            usage = data["usage"]
            return usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        return 0, 0
    except Exception:
        return 0, 0
```

### 2. 异步记录方法
```python
async def async_log_request_tokens(self, model_alias: str, input_tokens: int, output_tokens: int):
    """异步记录请求token到数据库"""
    try:
        import time
        timestamp = time.time()
        await asyncio.to_thread(
            self.monitor.add_model_request,
            model_alias,
            [timestamp, input_tokens, output_tokens]
        )
    except Exception as e:
        logger.error(f"异步记录token失败: {e}")
```

### 3. 流式响应包装器
```python
async def stream_with_token_logging(self, model_alias: str, response: any):
    """流式响应包装器，在结束后记录token使用"""
    content_chunks = []
    try:
        async for chunk in response.aiter_bytes():
            content_chunks.append(chunk)
            yield chunk
    finally:
        # 记录token使用情况
        full_content = b''.join(content_chunks)
        input_tokens, output_tokens = self.extract_tokens_from_response(full_content)
        await self.async_log_request_tokens(model_alias, input_tokens, output_tokens)
```

## 测试验证

创建了完整的测试套件验证功能：
- 监控器基本功能测试
- Token提取功能测试
- 异步记录功能测试

所有测试都通过，证明实现是可靠的。

## 数据库表结构

监控器会为每个模型创建以下表：
- `{model_safe_name}_requests`: 存储请求记录（时间戳、输入token数、输出token数）
- `{model_safe_name}_runtime`: 存储模型运行时间
- `{model_safe_name}_tier_pricing`: 存储阶梯计费配置
- `{model_safe_name}_hourly_price`: 存储按时计费价格

## 使用方法

1. **正常使用**：只需启动API服务器，token记录会自动工作
2. **查询统计**：可以通过监控器查询各模型的token使用情况
3. **计费管理**：可以设置不同的计费方式和价格

这个实现确保了在不影响路由流畅性和效率的前提下，能够准确地记录每个请求的token使用情况，为后续的统计分析和计费管理提供了可靠的数据基础。