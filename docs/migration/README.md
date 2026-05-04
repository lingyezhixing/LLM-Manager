# V2 → V3 后端迁移总纲

## 原则

1. **分层迁移，逐层验证**：每个 Phase 完成后必须通过全部测试，bug 不传递到下一阶段
2. **后端优先**：本迁移只涉及后端核心功能，前端相关 API 待后端稳定后再重构
3. **保持可运行**：每个 Phase 完成后，`python -m llm_manager` 应能正常启动
4. **V3 架构优先**：迁移时遵循 V3 的分层设计（api/ → services/ → database/ → schemas/），不引入 V2 的耦合模式

## 阶段总览

| Phase | 层级 | 核心任务 | 依赖 | 文档 |
|-------|------|---------|------|------|
| 1 | 数据库 | Schema 升级（7 张表）+ Repository 改写 | 无 | [01-database-layer.md](01-database-layer.md) |
| 2 | 配置 | 自适应部署选择 + 校验增强 | 无 | [02-config-system.md](02-config-system.md) |
| 3 | 插件 | 设备/接口插件迁移 + validate_request | Phase 2 | [03-plugin-system.md](03-plugin-system.md) |
| 4 | 模型管理 | 自适应启动 + 运行记录 + 空闲检测 | Phase 1,2,3 | [04-model-manager.md](04-model-manager.md) |
| 5 | 请求路由 | TokenTracker + 智能启动 + 流式 token 提取 | Phase 1,3,4 | [05-request-router-tokens.md](05-request-router-tokens.md) |
| 6 | 应用集成 | 完整启动链路 + 优雅关闭 + E2E 测试 | Phase 1-5 | [06-application-integration.md](06-application-integration.md) |

### 依赖关系图

```
Phase 1 (数据库) ──┐
                    ├──→ Phase 4 (模型管理) ──┐
Phase 2 (配置) ─┤                              ├──→ Phase 6 (应用集成)
                └──→ Phase 3 (插件) ──┘        │
                                              Phase 5 (请求路由) ──┘
```

Phase 1 和 Phase 2 可并行执行（互不依赖）。Phase 3 依赖 Phase 2。Phase 4 依赖 1+2+3。Phase 5 依赖 1+3+4。Phase 6 依赖所有前置。

## 每个阶段的验证流程

```
编写/修改代码 → 编写/更新测试 → 运行全部测试 → 通过 → 进入下一阶段
                                         ↓ 失败
                                      修复 bug → 重新测试
```

**关键规则**：
- 进入 Phase N+1 之前，Phase 1~N 的所有测试必须全部通过
- 如果修复 Phase N 的 bug 时影响了 Phase <N 的功能，必须重新运行受影响阶段的测试
- 每个 Phase 的测试文件独立存放于 `tests/test_*.py`，可单独运行也可批量运行

## V2 核心文件参考

| V2 文件 | 行数 | 核心功能 |
|---------|------|---------|
| `main.py` | 440 | 应用入口 |
| `core/api_server.py` | 1186 | API 端点 + 计费计算 |
| `core/api_router.py` | ~300 | 请求路由 + TokenTracker |
| `core/model_controller.py` | 1081 | 模型生命周期 + LogManager |
| `core/data_manager.py` | 701 | 数据库操作 + 计费配置 |
| `core/config_manager.py` | 279 | 配置加载 + 自适应部署 |
| `core/process_manager.py` | 526 | 进程管理 |
| `core/plugin_system.py` | 515 | 插件管理 + 设备缓存 |

## 迁移后未包含的功能（后续迭代）

| 功能 | 说明 | 触发条件 |
|------|------|---------|
| 计费计算引擎 | `_calculate_cost_vectorized()` 向量化成本计算 | 需要计费 UI 时 |
| 统计分析 API | throughput/cost_trends/token_trends | 需要分析仪表盘时 |
| 日志流推送 | LogManager + SSE 实时日志 | 需要日志查看 UI 时 |
| 数据管理 | 孤立模型清理、存储统计 | 需要数据管理 UI 时 |
| 配置热重载 | ConfigChanged 事件 + 运行时更新 | 需要不重启修改配置时 |
| WebUI 适配 | 前端 API 路径对接 | 后端全部完成后 |
| 健康监控重启 | 自动检测并重启异常模型 | 需要高可用性时 |
