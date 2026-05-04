# LLM-Manager V3 框架重构设计

## Context

LLM-Manager V2 从一个小型本地 LLM 管理工具发展到 ~6600 行 Python + React WebUI。虽然经历过V1 - V2的一次重构，但V2 架构依然存在核心模块过大、模块间紧耦合、插件系统重复抽象、缺乏数据库 migration 等问题，已无法支撑进一步开发。

V3 重构建立了分层架构脚手架，但在命名上有设计缺陷（`models/` 与 LLM 模型歧义、`extensions/` vs `plugins/` 语义重叠、`infra/` 目录不合理等）。

目前的V3版 在 V3方案版的基础上修正命名并完善 DI 容器，形成当前最终版本。

**定位**：以个人使用为主，架构上保持扩展性以便将来开源。

---

## 目录结构（V3 最终版）

```
llm_manager/
├── __init__.py
├── __main__.py                    # python -m llm_manager 入口
├── app.py                         # Application 类：启动/关闭生命周期
├── container.py                   # 轻量DI容器（自动装配、循环检测、拓扑排序）
├── events.py                      # 事件总线（单文件，Event + EventBus）
├── tray.py                        # 系统托盘（单文件，不需要目录）
│
├── schemas/                       # 纯数据类（原 models/，避免与 LLM 模型混淆）
│   ├── device.py                  # DeviceStatus, DeviceState
│   ├── model.py                   # ModelConfig, ModelInstance, ModelState
│   ├── request.py                 # TokenUsage, RequestRecord
│   └── billing.py                 # BillingConfig, BillingMode, CostRecord
│
├── config/                        # 配置系统
│   ├── models.py                  # Pydantic 配置模型（ProgramConfig, AppConfig）
│   ├── loader.py                  # ConfigLoader 抽象 + YamlConfigLoader
│   └── events.py                  # ConfigChanged 事件
│
├── database/                      # 数据库基础设施 + 数据访问（原 db/ + repositories/）
│   ├── engine.py                  # DatabaseEngine（SQLAlchemy Core，SQLite）
│   ├── schema.py                  # 表定义
│   └── repos/                     # Repository 模式（数据访问归属于数据库层）
│       ├── base.py                # BaseRepository 泛型基类
│       ├── model_repo.py          # 模型运行记录
│       ├── request_repo.py        # 请求/Token记录
│       └── billing_repo.py        # 计费配置
│
├── services/                      # 服务层（业务逻辑）
│   ├── base.py                    # BaseService 生命周期基类
│   ├── model_manager.py           # 模型生命周期管理
│   ├── device_monitor.py          # 设备状态聚合
│   ├── request_router.py          # OpenAI兼容请求路由
│   ├── process_manager.py         # 跨平台进程管理
│   ├── billing.py                 # 计费
│   └── monitor.py                 # 运行时监控
│
├── api/                           # API层
│   ├── app.py                     # FastAPI 应用工厂
│   ├── dependencies.py            # DI → FastAPI Depends 桥接
│   ├── middleware/
│   │   ├── error_handler.py       # 全局异常处理
│   │   └── logging.py             # 请求日志
│   └── routes/
│       ├── models.py              # 模型管理端点
│       ├── proxy.py               # OpenAI兼容转发
│       ├── devices.py             # 设备状态
│       ├── billing.py             # 计费（骨架）
│       ├── analytics.py           # 分析（骨架）
│       └── system.py              # 系统信息
│
├── plugins/                       # 插件系统（基类 + 实现统一管理）
│   ├── base_device.py             # DevicePlugin ABC
│   ├── base_interface.py          # InterfacePlugin ABC
│   ├── registry.py                # PluginRegistry
│   ├── loader.py                  # 插件发现、加载、校验
│   ├── devices/                   # 设备插件实现
│   │   ├── cpu.py
│   │   ├── nvidia.py
│   │   └── amd.py
│   └── interfaces/                # 接口插件实现
│       ├── chat.py
│       ├── embedding.py
│       └── reranker.py
│
└── utils/
    ├── logger.py                  # 日志配置
    └── tokens.py                  # Token 计数工具
```

**V2 → V3 命名变更**：

| V2 | V3 | 原因 |
|----|----|------|
| `models/` | `schemas/` | 避免"models"与 LLM 模型混淆 |
| `events/` 目录 | `events.py` 单文件 | 两个类不值得开目录 |
| `db/` + `repositories/` | `database/repos/` | 数据访问归属于数据库层 |
| `plugins/` + `extensions/` | `plugins/` 统一 | 一个概念一个目录 |
| `infra/` | 拆除 | process 移入 services，tray 独立为顶层 |
| `*_service.py` | 去掉 `_service` 后缀 | `model_manager.py` 比 `model_service.py` 更具体 |

---

## 核心组件设计

### 1. DI容器 (`container.py`)

```python
class Container:
    def register(interface, factory, singleton=True)
    def register_instance(interface, instance)
    def resolve(interface) -> instance
    async def start_all()    # 按依赖拓扑排序启动
    async def stop_all()     # 逆序关闭
```

- 容器自身注册为实例：`c.register_instance(Container, c)`，服务构造函数声明 `container: Container` 即可自动注入
- 通过 `typing.get_type_hints` + `inspect.signature` 双重策略解析类型注解
- `inspect.Parameter.empty` 实际是一个类（`inspect._empty`），必须显式排除
- lambda 工厂不需要参数：`lambda: DatabaseEngine(config)`
- 循环依赖检测 + 拓扑排序

### 2. 服务基类 (`services/base.py`)

```python
class BaseService(ABC):
    def __init__(self, container: Container): self._container = container
    async def on_start(self) -> None
    async def on_stop(self) -> None
```

### 3. 配置系统 (`config/`)

Pydantic 配置模型 + YAML 加载器。加载时校验失败会明确指出问题字段。

### 4. 事件总线 (`events.py`)

`Event` 基类 + `EventBus`（pub/sub）。handler 异常隔离，不阻断其他 handler。

### 5. 插件系统 (`plugins/`)

- 基类（`base_device.py`, `base_interface.py`）和实现在同一目录下
- `PluginLoader.discover()` 扫描目录，`load()` 实例化，`validate()` 校验
- 消除了旧系统的双重基类问题

### 6. 数据库层 (`database/`)

- `DatabaseEngine` — SQLAlchemy Core，默认 SQLite，WAL 模式
- `schema.py` — 表定义
- `repos/` — Repository 模式的数据访问实现

### 7. API层 (`api/`)

FastAPI 应用工厂 + 路由按功能域拆分。通过 `Depends(get_service(XxxService))` 桥接 DI 容器。

### 8. 应用启动 (`app.py`)

```
配置加载 → 容器注册（Container自身 + 所有服务） → start_all
→ 插件发现/加载 → FastAPI 应用创建 → 系统托盘 → uvicorn 启动
```

---

## 依赖规则

```
api/  →  services/  →  database/repos/  →  database/
           ↕                  |
       events.py              |
           ↑                  |
        config/            schemas/
                         (纯数据)
```

- `schemas/` 无依赖，被所有层引用
- `events.py` 零外部依赖
- `plugins/` 基类只依赖 `schemas/`
- `plugins/devices/` 和 `plugins/interfaces/` 只依赖基类和 `schemas/`
- 严禁反向依赖和循环依赖

---

## 统计

- 60 个 Python 文件，~2300 行代码
- 所有文件 < 300 行（最大 `model_manager.py` 187 行）
- 20 项自动化测试全部通过
