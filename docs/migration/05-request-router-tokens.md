# Phase 5: 请求路由与 Token 追踪迁移

## 目标

将 V2 的 `APIRouter`（请求路由 + 智能启动控制）和 `TokenTracker`（token 提取 + 异步记录）迁移到 V3，实现完整的请求代理链路：接收请求 → 自动启动模型 → 转发 → 提取 token → 异步记录数据库。

## 前置条件

- Phase 1 数据库层已完成（token 记录需要 `RequestRepository`）
- Phase 2 配置系统已完成（`should_track_tokens()`）
- Phase 3 插件系统已完成（接口插件的 `validate_request` + `extract_token_usage`）
- Phase 4 模型管理器已完成（自适应启动 + 运行记录）

## V2 → V3 差异分析

### 核心差异

| 功能 | V2 (api_router.py) | V3 (request_router.py) | 状态 |
|------|--------------------|-----------------------|------|
| 请求转发 | `route_request()` 完整实现 | `route_request()` 基本实现 | ⚠️ 需增强 |
| 智能启动 | 请求到达时自动启动 stopped 模型 | 无，返回 503 | ❌ 需迁移 |
| 流式 token 提取 | `extract_tokens_from_response()` 复杂解析 | `_track_request()` 简单提取 | ❌ 需迁移 |
| 待处理请求计数 | `pending_requests` dict | 无 | ❌ 需迁移 |
| token 异步记录 | `record_request_tokens()` via `asyncio.to_thread` | 无 | ❌ 需迁移 |
| timings 优先 | 优先从 `timings` 提取 cache_n/prompt_n | 无 | ❌ 需迁移 |

### V2 Token 提取机制详解

V2 的 `TokenTracker._extract_tokens(data)` 有两级提取策略：

```
1. 优先尝试 timings（llama.cpp 等推理引擎提供）
   - cache_n: 缓存命中 token（便宜）
   - prompt_n: 实际需要处理的 prompt token（贵）
   - predicted_n: 生成的 token
   → input_tokens = cache_n + prompt_n, output_tokens = predicted_n

2. 降级尝试 usage（OpenAI 兼容接口提供）
   - prompt_tokens + completion_tokens
   → 无 cache_n/prompt_n 区分

3. 全部为 0 → 忽略此请求
```

V2 的流式 token 提取（`extract_tokens_from_response()`）更复杂：
- 收集流式响应的所有 chunks
- 倒序扫描 SSE `data:` 行，找到最后包含 token 信息的块
- 支持 JSON 响应和 SSE 流两种格式
- 最多扫描末尾 10 个块寻找有效数据

## 迁移内容

### 5.1 新增 TokenTracker 服务

**新增文件**：`services/token_tracker.py`

```python
class TokenTracker(BaseService):
    """Token 提取与异步记录"""

    def __init__(self, container: Container):
        super().__init__(container)
        self._config: ProgramConfig | None = None
        self._request_repo: RequestRepository | None = None

    async def on_start(self) -> None:
        config = self._container.resolve(AppConfig)
        self._config = config.program
        self._request_repo = self._container.resolve(RequestRepository)

    def extract_tokens(self, data: dict) -> TokenUsage:
        """从解析后的 JSON dict 中提取 token"""
        # 1. 优先 timings
        if "timings" in data:
            timings = data["timings"]
            cache_n = timings.get("cache_n", 0)
            prompt_n = timings.get("prompt_n", 0)
            predicted_n = timings.get("predicted_n", 0)
            if cache_n or prompt_n or predicted_n:
                return TokenUsage(
                    prompt_tokens=cache_n + prompt_n,
                    completion_tokens=predicted_n,
                    total_tokens=cache_n + prompt_n + predicted_n,
                )

        # 2. 降级 usage
        if "usage" in data:
            usage = data["usage"]
            return TokenUsage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            )

        return TokenUsage()

    def extract_from_stream(self, content: bytes) -> TokenUsage:
        """从流式响应（SSE 或 JSON）中倒序提取 token"""
        # 迁移 V2 的 extract_tokens_from_response 逻辑
        # 1. 解码 bytes
        # 2. 识别 SSE 格式（data: 前缀）或 JSON 格式
        # 3. 倒序遍历最后 10 个块
        # 4. 对每个块调用 extract_tokens()
        ...

    async def record_request(
        self, model_name: str, usage: TokenUsage,
        start_time: float, end_time: float,
        cache_n: int = 0, prompt_n: int = 0,
    ) -> None:
        """异步记录 token 到数据库"""
        mode = ...  # 获取模型 mode
        if not self._config.should_track_tokens(mode):
            return
        if not any([usage.prompt_tokens, usage.completion_tokens, cache_n, prompt_n]):
            return

        await asyncio.to_thread(
            self._request_repo.save_request,
            model_name, start_time, end_time,
            usage.prompt_tokens, usage.completion_tokens,
            cache_n, prompt_n,
        )

    async def wrap_streaming_response(self, model_name: str, response, start_time: float):
        """流式响应包装器：转发数据 + 结束后提取 token"""
        chunks = []
        try:
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                yield chunk
        finally:
            end_time = time.time()
            await response.aclose()
            full_content = b"".join(chunks)
            usage = self.extract_from_stream(full_content)
            await self.record_request(model_name, usage, start_time, end_time)
```

### 5.2 增强 RequestRouter — 智能启动控制

**修改文件**：`services/request_router.py`

V2 的核心创新：请求到达时如果模型未运行，**路由层**负责自动启动，避免返回 503。

```python
async def route_request(self, model_name_or_alias, path, method, body, headers):
    resolved = self._model_manager.resolve_model_name(model_name_or_alias)
    if resolved is None:
        raise ValueError(f"Model '{model_name_or_alias}' not found")

    instance = self._model_manager.get_instance(resolved)

    # 智能启动控制
    if instance is None or instance.state != ModelState.RUNNING:
        if instance.state in (ModelState.STOPPED, ModelState.FAILED):
            await self._model_manager.start_model(resolved)
            instance = self._model_manager.get_instance(resolved)
        elif instance.state == ModelState.STARTING:
            # 等待启动完成
            while instance.state == ModelState.STARTING:
                await asyncio.sleep(0.5)
                instance = self._model_manager.get_instance(resolved)

    if instance.state != ModelState.RUNNING:
        raise RuntimeError(f"Model '{resolved}' is not running after startup attempt")

    # 原有转发逻辑 ...
```

### 5.3 增强 RequestRouter — Token 集成

在 `route_request` 和 `route_streaming` 中集成 `TokenTracker`：

```python
async def route_request(self, ...):
    # ... 启动 + 转发 ...

    response = await self._client.post(url, json=body, headers=headers)

    # Token 追踪
    if response.status_code == 200:
        try:
            data = response.json()
            usage = self._token_tracker.extract_tokens(data)
            await self._token_tracker.record_request(resolved, usage, start_time, time.time())
        except Exception:
            pass

    return response
```

### 5.4 待处理请求计数

```python
class RequestRouter(BaseService):
    def __init__(self, container):
        super().__init__(container)
        self._pending: dict[str, int] = {}  # model_name -> count

    def _increment_pending(self, name: str):
        self._pending[name] = self._pending.get(name, 0) + 1
        # 更新 last_request_at
        instance = self._model_manager.get_instance(name)
        if instance:
            instance.last_request_at = time.time()

    def _decrement_pending(self, name: str):
        if name in self._pending:
            self._pending[name] = max(0, self._pending[name] - 1)
```

### 5.5 注册到容器

在 `app.py` 中新增：

```python
container.register(TokenTracker, TokenTracker)
```

## 测试方案

### 测试文件：`tests/test_request_router.py`

```python
"""Phase 5 请求路由与 Token 追踪测试"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestTokenExtraction:
    """验证 token 提取逻辑"""

    def test_extract_from_timings(self):
        """优先从 timings 提取 cache_n/prompt_n"""
        tracker = TokenTracker(...)
        data = {"timings": {"cache_n": 80, "prompt_n": 20, "predicted_n": 50}}
        usage = tracker.extract_tokens(data)
        assert usage.prompt_tokens == 100  # cache_n + prompt_n
        assert usage.completion_tokens == 50

    def test_fallback_to_usage(self):
        """无 timings 时降级到 usage"""
        tracker = TokenTracker(...)
        data = {"usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}
        usage = tracker.extract_tokens(data)
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50

    def test_both_zero_returns_empty(self):
        """无 timings 无 usage 时返回空"""
        tracker = TokenTracker(...)
        usage = tracker.extract_tokens({})
        assert usage.total_tokens == 0


class TestStreamTokenExtraction:
    """验证流式响应的 token 提取"""

    def test_extract_from_sse_stream(self):
        """从 SSE 流的最后一个 data: 块提取 token"""
        sse_content = b'data: {"choices": []}\ndata: {"usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80}}\ndata: [DONE]\n'
        tracker = TokenTracker(...)
        usage = tracker.extract_from_stream(sse_content)
        assert usage.total_tokens == 80

    def test_extract_from_json_response(self):
        """从 JSON 响应提取 token"""
        json_content = b'{"usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}'
        tracker = TokenTracker(...)
        usage = tracker.extract_from_stream(json_content)
        assert usage.total_tokens == 150


class TestTokenRecording:
    """验证 token 异步记录到数据库"""

    @pytest.mark.asyncio
    async def test_record_to_database(self):
        """有效 token 应写入数据库"""
        # 验证 RequestRepository.save_request 被调用

    @pytest.mark.asyncio
    async def test_skip_when_mode_not_tracked(self):
        """不在追踪列表中的模式应跳过"""

    @pytest.mark.asyncio
    async def test_skip_when_all_zero(self):
        """全零 token 应跳过"""


class TestSmartAutoStart:
    """验证请求到达时的智能启动"""

    @pytest.mark.asyncio
    async def test_auto_start_stopped_model(self):
        """请求到达时自动启动已停止的模型"""
        # 准备：模型状态 STOPPED
        # 调用 route_request
        # 验证：start_model 被调用，请求成功转发

    @pytest.mark.asyncio
    async def test_wait_for_starting_model(self):
        """请求到达时等待正在启动的模型"""

    @pytest.mark.asyncio
    async def test_404_for_unknown_model(self):
        """未知模型应返回 404"""


class TestPendingRequestTracking:
    """验证待处理请求计数"""

    def test_increment_on_request_start(self): ...

    def test_decrement_on_request_end(self): ...
```

### 测试通过标准

1. `TestTokenExtraction` — timings 优先、usage 降级、空数据返回零
2. `TestStreamTokenExtraction` — SSE 和 JSON 格式均能正确提取
3. `TestTokenRecording` — 有效 token 写库、跳过不追踪模式、跳过全零
4. `TestSmartAutoStart` — 自动启动、等待启动中、未知模型报错
5. `TestPendingRequestTracking` — 计数增减正确

全部通过后，Phase 5 完成，进入 Phase 6。
