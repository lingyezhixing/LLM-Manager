# AGENTS.md — LLM-Manager 协作契约

> 本地单一事实源:架构分层、不变量、命令、配置链路、退出码。代码注释里的
> `spec §x / invariant N / guard D` 历史计划档案会随文档演进漂移,**以本文件为准**。
> 第三轮审查 round3 已据此清理(见 `docs/2026-08-04-code-review-round3.md`)。

## 1. 它是什么

本地多 LLM 模型的代理网关 + WebUI:按需启动/空闲回收本地模型进程(llama.cpp /
lmdeploy / vLLM …),对外暴露 OpenAI / Anthropic / Responses 兼容 API,记录用量与计费,
提供系统配置、模型管理、用量统计、日志查看的前端。**完全离线**(无云端依赖)。

- 后端:Python 3 + FastAPI + uvicorn + SQLite(单连接 + `write_lock`)。`src/llm_manager/`
- 前端:React 19 + Vite + TS + Tailwind v4 + TanStack Query。`frontend/`
- 进程:单 Python 进程跑一个 app(见不变量 1)。8080 端口同时 serve API 与前端构建产物
  (`frontend/dist`,**非实时源码**——改前端后须 `npm run build` 或看 Vite dev 端口)。

## 2. 架构分层(依赖单向无环)

```
config   ── 纯数据 + validate(YAML/DB → frozen dataclasses;设备名 _norm_device 归一一次)
  ↓
state    ── 内存状态机(ModelStatus)+ 单派发 inflight Future + activity(无锁,单线程)
  ↓
supervisor ── 子进程管理(_procs/_exit_cbs/_readers 三表 + kill_tree + 单 _wait 协程)
  ↓
runtime  ── lifecycle(编排)/scheduling(纯函数资源决策)/heartbeat(30s)/log_retention
  ↓
data     ── persistence(schema/迁移)+ logs(会话/行/SSE 广播)+ usage(计费)+ config_store(DB 配置)
  ↓
gateway  ── proxy(流式代理 + 用量计量)+ api/*(REST/SSE 端点)+ aliases(别名解析)
  ↓
tray     ── 系统托盘(自重启触发 / WOL / Claude 预设应用)
```

**分层纪律**:上层依赖下层,绝不反向。`scheduling.py` 纯函数(决策与副作用分离,
无 IO,单测无需 fake)。并发靠「单事件循环 + 临界段内无 await」保证(见不变量 2)。

## 3. 不变量(改动时勿破坏)

1. **单进程单 app**。模块级单例(见 §7)的前提——进程内只有一个 running app,
   故内存状态无需跨实例协调。多 worker/进程模型会破坏 live 集语义(见不变量 6)。
2. **单事件循环 + 无 await 临界段**。asyncio 单线程 → loop-resident 状态(state、
   logs live 集)无锁;唯有跨线程资源(sqlite)用 `write_lock` 串行化,写路径 `to_thread`。
3. **owner-token 单派发**。`state.claim_start` 原子占位:首个 caller 跑启动 pipeline,
   其余 await 同一 Future。`finish_start(owner=fut)` 防孤儿 winner(slow probe + 并发重启)
   覆盖新 owner——`owner != _inflight[name]` 时 no-op。
4. **协作式中断**。stop 不强杀:置 `stop_event`,pipeline 在 await 点自行检查并收口
   (kill_tree + `_log_end` + `_runtime_end`)。`force=True` 仅用于 STOPPED 转移(用户 stop)。
5. **退出码 81 = 请求重启**。`POST /api/config/restart` 置 `restart_requested`;
   `main()` 末尾据此 `sys.exit(81)`,**监督器在 81 上重启**(见 §5)。dev(`--reload`)
   无 server 分支则 `os._exit(81)`,由 `Dev-Backend.bat` 的 `if %ERRORLEVEL% EQU 81 goto restart` 兜底。
6. **运行中 = 内存 live 集,非 `end_time IS NULL`**。崩溃随进程消失,故残留会话/段天然
   落为 ended。心跳每 30s 把运行中项的 `end_time` 推到 now(只管时间,不管状态)。
   → **绝不能用 `end_time is not None` 判运行中**(曾致日志页运行中消失 bug,194962b)。
   日志会话 status = `CASE WHEN id IN live_session_ids() THEN 'running' ELSE 'ended'`。
7. **`aliases[0]` = 下游 served name**。模型 `aliases` 有序,首个即 lmdeploy `--model-name` /
   llama.cpp `-a` 的服务名;客户端请求按任意别名路由,但 served name 固定为 aliases[0]。
8. **DB 配置单一源**。运行时只读 DB 快照(`ConfigStore.snapshot()`,frozen)。env
   (`LLM_MANAGER_*`)在启动期写库(`apply_env_overrides`),不直接覆盖运行变量。
   YAML `config.load()` 仅作首次导入(空库时)。

## 4. 配置写回路径

```
前端 PUT /api/config/*  →  Pydantic 校验(422)  →  set_settings(多键原子写)
                         →  mutate_appconfig(fn):锁内 read→改→validate→write
                         →  ConfigStore.reload()(刷 frozen 快照)
                         →  消费方:热字段(alive_time/log_retention)每轮读 fresh;
                                   重启字段(host/port/log_level/claude_settings_path)
                                   需 exit 81 重启生效(顶部横幅提示 restart_fields)
```

- **模型 CRUD**:`mutate_appconfig` 全量替换模型世界(DELETE+INSERT,id churn 可接受),
  validate 失败 raise `ConfigValidationFailed`(→422,不落脏数据)。
- **`required_devices ⊄ memory_mb`** 是软告警(`scheme_memory_warnings`,非 fatal):
  调度时该设备按 0 需求、显存检查被架空;部分合法配置刻意不填,故仅日志告警。

## 5. 退出码 / 自重启契约

- 生产:`LLM-Manager.bat` 监督器循环,`main()` 以 81 退出 → 重启。
- dev:`Dev-Backend.bat` `:restart` + 81 循环;`os._exit(81)` 跳过 lifespan 收尾
  (dev 进程一次性,可接受)。
- 托盘菜单/`POST /api/config/restart` 均走 `restart_requested → exit 81`。

## 6. 命令(验收用)

后端(项目根,conda env `LLM-Manager`):
```bash
python -m pytest tests -q          # 全量(含 smoke);~22s
ruff check src tests               # lint
pyright src/llm_manager            # 类型检查(0 errors 基线)
```
前端(`frontend/`):
```bash
npm run build        # = tsc -b && vite build;改前端后必跑(8080 serve dist)
npx oxlint src       # lint(存量 2 warning:toast/dialog 的 only-export-components,已知)
npx tsc -b           # 仅类型检查
```

## 7. 模块级单例(单进程前提 = 不变量 1)

| 模块 | 单例 | 说明 |
|---|---|---|
| `state` | `_state` / `_inflight` | 模型状态机 + 单派发 Future |
| `data.logs` | `_sessions` / `_pending` / `_db` / `_flush_chain` | 日志会话 live 集 + 待落库 + flush 串行链 |
| `data.usage` | `_live_segments` | 运行中计费段(崩溃随进程消失) |
| `devices` | `_LHM_COMPUTER`(LibreHardwareMonitor) | 780M 核显传感器单例 |
| `session` | 模型会话追踪 | alias↔session 映射 |

测试有 `_reset()` 接缝清空(state/logs/usage)。**新增模块级可变状态前先想清楚**:
它隐式假设「整个进程只有一个 app 实例」,破坏该假设会牵连 live 集语义。

## 8. 工作流约束

- **本地仓库,严禁推 origin**(用户明确)。小任务直接在 `main`;较大特性开 feature 分支
  (非 worktree)FF-merge + 删分支。commit message 末尾加 `Co-Authored-By: Claude <noreply@anthropic.com>`。
- **完全离线**:图标(lucide 内联)、字体(系统字体)、资产严禁 CDN 引用。
- **派 subagent 一律用最强模型**(fable),勿按成本降级。

## 9. 已知遗留 / 已评估 DEFER(非阻塞,按节奏渐进)

- **S2 前后端类型生成(已建立,渐进迁移)**:`scripts/gen_types.py` 从 FastAPI OpenAPI 生成
  `frontend/src/lib/api/schema.d.ts`(纯 stdlib,无 npm 依赖——openapi-typescript 官方只支持 TS 5,
  本项目用 TS 6;且须离线)。`npm run gen-types` 重生成(需 llm_manager 可导入的 python 环境)。
  **已迁移**:usage 响应类型(SessionUsage/UsageSummary/ByModelEntry/UsageSeries/CostByModel/CostSummary
  → schema.d.ts 别名,消费方零改动)。**待迁移**:logs(LogLine/LogSession 的 stream/level/status 需后端
  补 Literal 才不丢精度)、config/models GET(返回裸 dict,需补 `response_model`)、请求体(ModelDefInput 等,
  注意有默认值的字段会生成成可选)。改后端响应模型后跑 `npm run gen-types`。
- **S3 CI / pre-commit**:`ruff format --check && ruff check && pyright && pytest -q` +
  前端 `oxlint && tsc -b`。项目装了 ruff 但未强制 format(37/42 文件会重排)。**待用户定**
  是否一次性 `ruff format` + 纳入 CI。
- **S5 `useSyncedForm<T>` 抽象**:general/wol/claude/model-def-form 四处手写「服务端快照→本地表单」
  同步(已滋生 F1)。抽象 hook 把「baseline 只能在 onSuccess 推进」固化。重构面较大,**留作演进**。
- **🔵1 create_task 任务集**:6 处 fire-and-forget 任务内部均已捕异常,实际未检索异常风险低;
  跨模块引用集 helper 性价比不足。
- 其它:双账本(内存计数 + DB 落库)、`_migrate` 历史链退役窗口、前端 `useLogViewer` 改 useReducer
  + 虚拟化、时长格式化函数收敛——均纯清理,不影响正确性。
