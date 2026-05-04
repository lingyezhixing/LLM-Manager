# Phase 6: 应用集成与生命周期

## 目标

将所有已迁移的组件整合到 `Application` 类中，实现完整的后端启动链路：配置加载 → DI 装配 → 服务启动 → 插件加载 → 模型自启 → 请求代理 → 优雅关闭。

## 前置条件

- Phase 1-5 全部完成并通过测试

## 迁移内容

### 6.1 完善容器注册（app.py）

当前 `app.py` 的 `_create_container()` 缺少 Phase 1-5 新增的组件：

```python
def _create_container(self, config: AppConfig) -> Container:
    container = Container()
    container.register_instance(Container, container)
    container.register_instance(AppConfig, config)

    # 基础设施
    container.register(EventBus, EventBus)
    container.register(PluginRegistry, PluginRegistry)
    container.register(DatabaseEngine, lambda: DatabaseEngine(config.program))

    # Repositories
    container.register(ModelRuntimeRepository, ModelRuntimeRepository)
    container.register(ProgramRuntimeRepository, ProgramRuntimeRepository)
    container.register(RequestRepository, RequestRepository)
    container.register(BillingRepository, BillingRepository)

    # Services
    container.register(ProcessManager, ProcessManager)
    container.register(DeviceMonitor, DeviceMonitor)
    container.register(TokenTracker, TokenTracker)
    container.register(ModelManager, ModelManager)
    container.register(RequestRouter, RequestRouter)
    container.register(IdleMonitor, IdleMonitor)
    container.register(BillingService, BillingService)
    container.register(MonitorService, MonitorService)

    return container
```

### 6.2 完善启动流程

当前 `_async_run()` 需要补充：

1. **数据库初始化**：`start_all()` 之后，调用 `DatabaseEngine` 的计费默认值 seed
2. **程序运行记录**：启动时记录 `program_runtime.start`，关闭时更新 `end`
3. **接口插件 TokenTracker 注入**：确保 TokenTracker 在 RequestRouter 之前启动

```python
async def _async_run(self) -> None:
    config = self._load_config()
    setup_logging(level=config.program.log_level)

    container = self._create_container(config)
    self._container = container

    await container.start_all()

    # 初始化计费默认值
    db_engine = container.resolve(DatabaseEngine)
    db_engine.initialize_billing(config)

    # 记录程序启动时间
    program_repo = container.resolve(ProgramRuntimeRepository)
    self._program_runtime_id = program_repo.record_start(time.time())

    # 加载插件
    self._load_plugins(config, container)

    # 自动启动模型
    model_mgr = container.resolve(ModelManager)
    models_to_start = [
        name for name, inst in model_mgr.get_all_instances().items()
        if inst.config.auto_start
    ]
    if models_to_start:
        asyncio.create_task(model_mgr.start_auto_start_models())

    # 启动 API
    app = create_api_app(container)
    server_config = uvicorn.Config(
        app,
        host=config.program.host,
        port=config.program.port,
        log_level=config.program.log_level.lower(),
    )
    server = uvicorn.Server(server_config)

    self._start_tray(container)

    try:
        await server.serve()
    except Exception:
        logger.exception("Server error")
    finally:
        await self._shutdown(container)
```

### 6.3 完善关闭流程

```python
async def _shutdown(self, container: Container) -> None:
    logger.info("Shutting down...")

    # 更新程序运行记录
    if hasattr(self, '_program_runtime_id') and self._program_runtime_id:
        program_repo = container.resolve(ProgramRuntimeRepository)
        program_repo.update_end(self._program_runtime_id, time.time())

    # 停止所有运行中的模型（更新运行记录）
    model_mgr = container.resolve(ModelManager)
    for name, inst in model_mgr.get_all_instances().items():
        if inst.state == ModelState.RUNNING:
            try:
                await model_mgr.stop_model(name)
            except Exception:
                logger.exception("Failed to stop model '%s' during shutdown", name)

    # 逆序停止所有服务
    await container.stop_all()
    logger.info("LLM-Manager stopped")
```

### 6.4 API 路由整合

将 V3 的 API 路由注册到 FastAPI 应用中，确保后端核心功能的 API 可用：

**保留现有 V3 路由**：
- `/api/models` — 模型管理
- `/api/proxy` — 请求代理
- `/api/devices` — 设备状态
- `/api/system` — 系统信息
- `/health` — 健康检查

**暂不迁移**（前端相关，等后端稳定后再重构）：
- `/api/billing/...` — 计费配置 API
- `/api/analytics/...` — 统计分析 API
- `/api/logs/...` — 日志流 API
- `/api/data/...` — 数据管理 API
- `/v1/chat/completions` 直接路由（V3 用 `/api/proxy/v1/...`）

### 6.5 系统托盘增强

当前 V3 的 `SystemTray` 已有基本功能（打开 WebUI、退出）。V2 额外有：
- 重启自动启动模型
- Claude 配置切换
- Wake-on-LAN

**本轮只保留基本功能**，高级功能后续迁移。

## 测试方案

### 端到端测试文件：`tests/test_application_e2e.py`

```python
"""Phase 6 应用端到端测试"""
import pytest
import asyncio
import tempfile
from pathlib import Path


class TestApplicationStartup:
    """验证完整启动链路"""

    @pytest.mark.asyncio
    async def test_full_startup_with_config(self, tmp_path):
        """使用真实 config.yaml 启动，验证所有服务初始化"""
        # 准备最小 config.yaml
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
program:
  host: "127.0.0.1"
  port: 18080
  alive_time: 0
Local-Models: {}
""")
        app = Application(str(config_file))
        # 只测试到容器启动，不实际启动 uvicorn
        config = app._load_config()
        container = app._create_container(config)
        await container.start_all()

        # 验证所有服务已启动
        assert container.resolve(ModelManager) is not None
        assert container.resolve(RequestRouter) is not None
        assert container.resolve(TokenTracker) is not None
        assert container.resolve(DeviceMonitor) is not None

        await container.stop_all()

    @pytest.mark.asyncio
    async def test_program_runtime_recorded(self, tmp_path):
        """启动和关闭时应记录程序运行时间"""
        # 准备 config, 启动, 关闭
        # 验证 program_runtimes 表有记录且 end_time 被更新


class TestGracefulShutdown:
    """验证优雅关闭"""

    @pytest.mark.asyncio
    async def test_running_models_stopped_on_shutdown(self):
        """关闭时所有运行中的模型应被停止"""

    @pytest.mark.asyncio
    async def test_services_stopped_in_reverse_order(self):
        """服务应按逆序停止"""


class TestEndToEndProxy:
    """端到端代理测试（需要模拟后端推理服务）"""

    @pytest.mark.asyncio
    async def test_proxy_request_to_running_model(self):
        """向运行中的模型发送请求应成功"""
        # 需要：模拟模型进程 + 模拟推理服务
        # 验证：请求被正确转发，token 被记录

    @pytest.mark.asyncio
    async def test_auto_start_and_proxy(self):
        """向已停止的模型发送请求应自动启动并转发"""

    @pytest.mark.asyncio
    async def test_token_recorded_after_request(self):
        """请求后 token 应记录到数据库"""
        # 验证 model_requests 表有新记录
```

### 测试通过标准

1. `TestApplicationStartup` — 所有服务正确初始化，程序运行时间被记录
2. `TestGracefulShutdown` — 模型被停止，服务逆序关闭
3. `TestEndToEndProxy` — 请求正确转发，token 正确记录

全部通过后，V3 后端核心功能迁移完成。

---

## Phase 6 完成后的状态

### 已迁移的核心功能

- ✅ 配置加载与校验（Pydantic + YAML）
- ✅ DI 容器与服务生命周期
- ✅ 数据库层（7 张表 + Repository 模式）
- ✅ 自适应部署配置选择
- ✅ 插件系统（设备 + 接口）
- ✅ 模型生命周期管理（启停 + 状态机 + 运行记录）
- ✅ 请求路由与代理（智能启动 + 流式支持）
- ✅ Token 追踪（timings 优先 + usage 降级 + 流式提取）
- ✅ 空闲检测自动停止
- ✅ 优雅关闭

### 暂未迁移（等后端稳定后）

- ⏳ 计费计算引擎（`_calculate_cost_vectorized`）
- ⏳ 统计分析 API（throughput, cost_trends, token_trends）
- ⏳ 日志流推送（`LogManager` + SSE）
- ⏳ 数据管理 API（孤立模型、存储统计）
- ⏳ WebUI 前端适配
- ⏳ 配置热重载
