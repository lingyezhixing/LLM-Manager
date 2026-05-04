# Phase 4: 模型管理器迁移

## 目标

将 V2 `ModelController` 的核心模型生命周期管理逻辑迁移到 V3 的 `ModelManager`，包括自适应部署选择、进程输出捕获、运行时间记录、空闲检测。

## 前置条件

- Phase 1 数据库层已完成（模型运行记录需要 `ModelRuntimeRepository`）
- Phase 2 配置系统已完成（自适应部署选择依赖 `select_deployment()`）
- Phase 3 插件系统已完成（设备检测、接口健康检查）

## V2 → V3 差异分析

### 功能差异

| 功能 | V2 (ModelController) | V3 (ModelManager) | 状态 |
|------|---------------------|-------------------|------|
| 启停模型 | `start_model()` / `stop_model()` | ✅ 已有 | ✅ |
| 自适应部署 | 根据 online_devices 选方案 | 无，使用固定 deployment | ❌ 需迁移 |
| 并行启动 | `ThreadPoolExecutor` + futures | 无 | ❌ 需迁移 |
| Checkpoint 机制 | 启动中可立即中断 | 无 | ❌ 需迁移 |
| 运行时间记录 | 启动时 `add_model_runtime_start`，停止时 update | 无 | ❌ 需迁移 |
| 空闲检测 | `idle_check_loop()` 定时检查 last_access | 无 | ❌ 需迁移 |
| 日志流 | `LogManager` 实时捕获 stdout | 无 | ⏳ 后续（前端相关） |
| 僵尸进程清理 | 启动前检查残留进程 | 无 | ⚠️ 可选迁移 |

### V2 模型启动流程详解

```
start_model(primary_name):
  1. 获取锁，检查状态（防止重复启动）
  2. 获取在线设备列表 → plugin_manager.update_device_status()
  3. 调用 config_manager.get_adaptive_model_config(name, online_devices)
  4. 找到匹配的部署方案（script_path, memory_mb, required_devices）
  5. 状态设为 INIT_SCRIPT
  6. 调用 process_manager.start_process(name, script_path)
  7. 捕获 stdout/stderr（回调给 LogManager）
  8. 状态设为 HEALTH_CHECK
  9. 调用 interface_plugin.health_check(port, timeout)
 10. 状态设为 ROUTING
 11. 记录 model_runtime_start 到数据库
 12. 释放锁
```

V3 当前流程更简单，缺少步骤 2-4（自适应部署）和步骤 11（数据库记录）。

## 迁移内容

### 4.1 集成自适应部署选择

**修改文件**：`services/model_manager.py`

改造 `start_model()` 方法：

```python
async def start_model(self, name: str) -> ModelInstance:
    with self._lock:
        instance = self._instances.get(name)
        if instance is None:
            raise ValueError(f"Model '{name}' not found")
        if instance.state == ModelState.RUNNING:
            return instance
        if instance.state == ModelState.STARTING:
            raise RuntimeError(f"Model '{name}' is already starting")

    # 获取在线设备
    device_statuses = self._device_monitor.get_all_statuses()
    online_devices = {name for name, s in device_statuses.items() if s.state == DeviceState.ONLINE}

    # 自适应选择部署方案
    entry = self._app_config.models[name]
    result = entry.select_deployment(online_devices)
    if result is None:
        raise RuntimeError(f"No suitable deployment for '{name}' with devices {online_devices}")

    deployment_name, deployment_entry = result
    deployment = DeploymentConfig(
        required_devices=deployment_entry.required_devices,
        script_path=deployment_entry.script_path,
        memory_mb=deployment_entry.memory_mb,
    )

    with self._lock:
        instance.state = ModelState.STARTING
        instance.active_deployment = deployment_name

    try:
        process_info = await self._process_manager.start_process(
            name=name,
            script_path=str(deployment.script_path),
        )
        instance.pid = process_info.pid
        instance.started_at = time.time()

        interface_plugin = self._plugin_registry.get_interface(instance.config.mode)
        if interface_plugin:
            healthy = await interface_plugin.health_check(instance.config.port)
            if not healthy:
                logger.warning("Health check failed for model '%s'", name)

        instance.state = ModelState.RUNNING
        instance.last_request_at = None

        # 记录运行时间到数据库
        model_repo = self._container.resolve(ModelRuntimeRepository)
        runtime_id = model_repo.record_start(name, instance.started_at)
        instance._runtime_record_id = runtime_id

        await self._event_bus.publish(_ModelStarted(model_name=name, port=instance.config.port))
        logger.info("Model '%s' started on port %d (deployment: %s)", name, instance.config.port, deployment_name)
        return instance

    except Exception:
        with self._lock:
            instance.state = ModelState.FAILED
        raise
```

**注意**：`ModelInstance` dataclass 需要新增 `_runtime_record_id` 字段（内部使用，不暴露给 API）。

### 4.2 运行时间记录

**修改文件**：`schemas/model.py` — `ModelInstance` 新增字段

```python
@dataclass
class ModelInstance:
    # ... 现有字段 ...
    _runtime_record_id: int | None = None  # 内部：数据库运行记录 ID
```

**修改文件**：`services/model_manager.py` — `stop_model()` 中更新运行记录

```python
async def stop_model(self, name: str) -> ModelInstance:
    # ... 现有逻辑 ...

    try:
        await self._process_manager.stop_process(name)
        instance.state = ModelState.STOPPED

        # 更新运行时间记录
        if instance._runtime_record_id:
            model_repo = self._container.resolve(ModelRuntimeRepository)
            model_repo.record_end_by_id(instance._runtime_record_id, time.time())

        # ... 发布事件等 ...
```

`record_end_by_id(record_id, end_time)` 已在 Phase 1 实现。

### 4.3 空闲检测循环

V2 的 `idle_check_loop()` 每 60 秒检查一次，如果模型超过 `alive_time` 分钟没有活动（`last_access` 为 0），则自动停止。

**新增文件**：`services/idle_monitor.py`

```python
class IdleMonitor(BaseService):
    """空闲模型自动停止监控"""

    def __init__(self, container: Container):
        super().__init__(container)
        self._running = False
        self._task: asyncio.Task | None = None

    async def on_start(self) -> None:
        config = self._container.resolve(AppConfig)
        alive_minutes = config.program.alive_time
        if alive_minutes <= 0:
            return  # 0 表示禁用空闲检测
        self._running = True
        self._task = asyncio.create_task(self._check_loop(alive_minutes))

    async def on_stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _check_loop(self, alive_minutes: int):
        while self._running:
            await asyncio.sleep(60)
            try:
                model_mgr = self._container.resolve(ModelManager)
                cutoff = time.time() - alive_minutes * 60
                for name, inst in model_mgr.get_all_instances().items():
                    if inst.state != ModelState.RUNNING:
                        continue
                    if inst.last_request_at is not None and inst.last_request_at < cutoff:
                        logger.info("Model '%s' idle for %d min, stopping", name, alive_minutes)
                        await model_mgr.stop_model(name)
            except Exception:
                logger.exception("Idle check failed")
```

**注册到容器**：在 `app.py` 中新增 `container.register(IdleMonitor, IdleMonitor)`。

### 4.4 更新 last_request_at

当前 `RequestRouter._track_request()` 已更新 `instance.last_request_at`。空闲检测只需要读取此字段即可。逻辑已就位，无需额外修改。

### 4.5 移除固定 deployment_name 参数

V3 当前 `start_model(name, deployment_name=None)` 允许手动指定部署方案。迁移后由自适应选择自动决定，但保留 `deployment_name` 参数作为手动覆盖：

```python
async def start_model(self, name: str, deployment_name: str | None = None) -> ModelInstance:
    # ...
    if deployment_name:
        # 手动指定方案
        deployment = instance.config.deployments.get(deployment_name)
        if deployment is None:
            raise ValueError(f"Deployment '{deployment_name}' not found for model '{name}'")
    else:
        # 自适应选择
        result = entry.select_deployment(online_devices)
        if result is None:
            raise RuntimeError(...)
        deployment_name, deployment_entry = result
        deployment = ...
```

## 测试方案

### 测试文件：`tests/test_model_manager.py`

```python
"""Phase 4 模型管理器测试"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from llm_manager.container import Container
from llm_manager.services.model_manager import ModelManager
from llm_manager.schemas.device import DeviceStatus, DeviceState
from llm_manager.schemas.model import ModelInstance, ModelState
from llm_manager.events import EventBus
from llm_manager.config.models import AppConfig, ProgramConfig, ModelConfigEntry


def make_container_with_config(models_config: dict) -> Container:
    """创建包含测试配置的容器"""
    container = Container()
    container.register_instance(Container, container)
    config = AppConfig(
        program=ProgramConfig(),
        models=models_config,
    )
    container.register_instance(AppConfig, config)
    container.register(EventBus, EventBus)
    # Mock 其他依赖
    container.register_instance(...)
    return container


class TestAdaptiveDeployment:
    """验证自适应部署选择"""

    @pytest.mark.asyncio
    async def test_select_gpu_deployment_when_available(self):
        """当 GPU 在线时应选择 GPU 方案"""
        # 准备：模型有 GPU 和 CPU 两个方案，GPU 设备在线
        # 验证：start_model 使用 GPU 方案的 script_path

    @pytest.mark.asyncio
    async def test_fallback_to_cpu_when_gpu_offline(self):
        """当 GPU 离线时应降级到 CPU 方案"""

    @pytest.mark.asyncio
    async def test_fail_when_no_deployment_matches(self):
        """当没有方案匹配时应抛出 RuntimeError"""


class TestRuntimeRecording:
    """验证运行时间数据库记录"""

    @pytest.mark.asyncio
    async def test_start_records_runtime_in_db(self):
        """启动模型时应写入 model_runtime 表"""
        # 验证 record_start 被调用，返回值保存在 instance._runtime_record_id

    @pytest.mark.asyncio
    async def test_stop_updates_runtime_end(self):
        """停止模型时应更新 model_runtime 的 end_time"""


class TestIdleMonitor:
    """验证空闲检测"""

    @pytest.mark.asyncio
    async def test_idle_model_auto_stopped(self):
        """超过 alive_time 的模型应被自动停止"""
        # 准备：alive_time=1, 模型 last_request_at 在 120 秒前
        # 等待检测周期
        # 验证：模型状态变为 STOPPED

    @pytest.mark.asyncio
    async def test_active_model_not_stopped(self):
        """活跃的模型不应被停止"""

    @pytest.mark.asyncio
    async def test_disabled_when_alive_time_zero(self):
        """alive_time=0 时不启动空闲检测"""


class TestModelStateTransitions:
    """验证状态机转换"""

    @pytest.mark.asyncio
    async def test_start_while_starting_raises(self):
        """STARTING 状态下再次启动应抛异常"""

    @pytest.mark.asyncio
    async def test_failed_after_start_exception(self):
        """启动失败后状态应为 FAILED"""

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        """停止非 RUNNING 模型应是幂等的"""
```

### 测试通过标准

1. `TestAdaptiveDeployment` — GPU 优先、CPU 降级、无匹配报错
2. `TestRuntimeRecording` — 启停时正确记录运行时间到数据库
3. `TestIdleMonitor` — 空闲自动停止、活跃不停止、alive_time=0 禁用
4. `TestModelStateTransitions` — 状态转换符合预期，边界情况正确

全部通过后，Phase 4 完成，进入 Phase 5。
