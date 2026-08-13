# AGENTS.md — LLM-Manager 协作契约

> 本地单一事实源:架构分层、不变量、命令、配置链路、退出码。代码注释里的
> `spec §x / invariant N / guard D` 历史计划档案会随文档演进漂移,**以本文件为准**。
> 第三轮审查 round3 已据此清理(见 `docs/2026-08-04-code-review-round3.md`)。

## 1. 它是什么

本地多 LLM 模型的代理网关 + WebUI:按需启动/空闲回收本地模型进程(llama.cpp /
lmdeploy / vLLM …),对外暴露 OpenAI / Anthropic / Responses 兼容 API,记录用量与计费,
提供系统配置、模型管理、用量统计、日志查看的前端。**完全离线**(无云端依赖)。
**唯一联网点 = 自更新**(系统页「更新」区,git fetch/merge 本项目仓库,用户显式
点击检查/应用按钮才触发,后台永不自动;见 §5.1)。

- 后端:Python 3 + FastAPI + uvicorn + SQLite(单连接 + `write_lock`)。`src/llm_manager/`
- 前端:React 19 + Vite + TS + Tailwind v4 + TanStack Query。`frontend/`
- 进程:单 Python 进程跑一个 app(见不变量 1)。8080 端口同时 serve API 与前端构建产物
  (`frontend/dist`,**非实时源码**——改前端后须 `npm run build` 或看 Vite dev 端口)。
  **dist 已入库**:改前端须重建并提交 dist,克隆即用、无需本地构建。

## 2. 架构分层(依赖单向无环)

```
config   ── 纯数据 + validate(YAML/DB → frozen dataclasses;设备名存储原样,匹配时归一化)
  ↓
state    ── 内存状态机(ModelStatus)+ 单派发 inflight Future + activity(无锁,单线程)
  ↓
supervisor ── 子进程管理(_procs/_exit_cbs/_readers 三表 + kill_tree + 单 _wait 协程)
  ↓
runtime  ── lifecycle(编排)/scheduling(纯函数资源决策)/background(心跳 30s + 日志保留 + 空闲回收 + 自启)/update(自更新:git 编排)
  ↓
data     ── persistence(schema/迁移)+ logs(会话/行 SQL + 捕获/广播/flush)+ usage(计费 + 会话计数)+ config_store(DB 配置)
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
5. **退出码 81 = 请求重启**(内部信号)。`POST /api/config/restart` 置 `restart_requested`;
   worker(`_run_worker`,=`--worker` 入口)据此 `sys.exit(81)`,**内置 parent 监督器
   (`_run_parent`)接住 81 拉起全新 worker**(见 §5)。dev(`--reload`)不经 main、绕过
   parent;无 server 分支则 `os._exit(81)`(dev 进程一次性,可接受)。
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
- **`required_devices ⊄ memory_mb`** 合法、不告警:缺条目的设备按 0 需求调度(该设备
  不做显存检查)——「设备仅用于方案匹配、真运行在别处」是合法用法,`{}` 与 `{dev:0}`
  调度语义等价;前端保存时会把 required 设备的缺省显存显式写 0(所见即所存)。

## 5. 自重启(parent + worker,类 NapCat)

- **架构**:`python -m llm_manager`(= `runner.py` 的 parent 监督器;常驻、不碰 DB、不持 app 状态);
  spawn `python -m llm_manager --worker`(=worker,跑 create_app + server.run)。
  worker 退出码 81 → parent 拉全新 worker(每次全新进程,OS 回收一切,构造性干净);
  0 → parent 退;其他(崩溃)→ parent 亦退,**不自愈**(可见失败)。
  严格顺序:parent 等 worker rc 到手才 spawn 下一个 → 无双 worker 并存、无端口竞争。
- **信号转发**:parent 收 Ctrl-C/SIGTERM → 转发 worker 进程组(Win `CTRL_BREAK_EVENT` /
  POSIX `killpg SIGTERM`)使其优雅关闭;`_SHUTDOWN_GRACE`(10s)超时强杀兜底。
- dev(`uvicorn --factory --reload`):不经 main、绕过 parent,uvicorn 自管 reload;
  `restart_app` 无 server 分支 `os._exit(81)`(dev 一次性)。
- `POST /api/config/restart`(WebUI 顶部重启横幅)走 `restart_requested → worker exit 81 → parent 拉新`。
- `LLM-Manager.bat` 仅作 Windows 静默后台启动(VBS),不参与重启。

### 5.1 自更新(标签版本 + 严格 ff-only)

- **版本 = git 标签**(当前 = `git describe --tags --abbrev=0 HEAD`,最新 =
  `origin/main` 最近可达标签)——以标签为版本号,不以单个 commit。发版必须打标签。
- **流程**:`GET /api/update/status`(fetch 对比,不动工作树;离线 → ok=False+error)/
  `POST /api/update/apply`(fetch + `git merge --ff-only origin/main` → `trigger_restart` →
  exit 81 → parent 拉新 worker。editable 安装下工作树即源码,新进程 import 即新代码)。
- **严格语义**:工作树脏 / 本地历史分叉 → 409,绝不 stash/覆盖。仅 ff-only。
- **网络纪律**:唯一联网点,仅用户显式按钮触发(系统页「更新」区),后台永不自动。
- 测试:`tests/unit/runtime/test_update.py`(本地 bare origin,无网络)、
  `tests/unit/gateway/test_api_update.py`(API 契约)。
- 注意:更新后依赖若变,editable 安装不会自动重装(pip 层自理)。

## 6. 命令(验收用)

后端(项目根,conda env `LLM-Manager`):
```bash
python -m pytest tests -q          # 全量(含 smoke);~23s
ruff format --check .             # 格式(2026-08-06 已全仓库格式化;改完须保持 format 干净)
ruff check .                     # lint(规则集显式固定 E4/E7/E9/F,见 pyproject;单路径防多路径丢诊断竞态)
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
| `data.logs` | `_sessions` / `_alias_to_session` / `_pending` / `_db` / `_flush_chain` | 日志会话 live 集 + alias↔会话映射 + 待落库 + flush 串行链 |
| `data.usage` | `_live_segments` / `_c` | 运行中计费段(崩溃随进程消失)+ 进程内用量计数器(重启清零,概览 session-stats 卡) |
| `devices` | `_LHM_COMPUTER`(LibreHardwareMonitor) | 780M/Intel 核显传感器单例(Windows);Linux Intel iGPU 走 i915 识别 + intel_gpu_top 采样、AMD 走 amdgpu sysfs(均无单例) |

测试接缝:state 有 `_reset()`、logs 有 `reset()`、usage 有 `_reset_counters()`(session 计数);
usage 的 `_live_segments` 由 `tests/unit/data/test_persistence.py` 的本地 fixture 直接清。
**新增模块级可变状态前先想清楚**:它隐式假设「整个进程只有一个 app 实例」,
破坏该假设会牵连 live 集语义。

## 8. 工作流约束

- **本地仓库,严禁推 origin**(用户明确)。小任务直接在 `main`;较大特性开 feature 分支
  (非 worktree)FF-merge + 删分支。commit message 末尾加 `Co-Authored-By: Claude <noreply@anthropic.com>`。
- **完全离线**:图标(lucide 内联)、字体(系统字体)、资产严禁 CDN 引用。
- **派 subagent 一律用最强模型**(fable),勿按成本降级。

## 9. 已知遗留 / 已评估 DEFER(非阻塞,按节奏渐进)

- **S2 前后端类型生成(已建后回退,2026-08-04)**:曾引入 `scripts/gen_types.py` 从 FastAPI OpenAPI
  生成 `schema.d.ts`(aef9887),评估后决定回退——单人开发 + 小类型面(~26 个手写 interface)+
  低改动频率下收益不足;且渐进迁移停在中间态 = 同一后端模型两份 TS 定义(schema.d.ts 20 个类型
  仅 6 个被消费,其余与手写版并存),双路径比纯手动更差。已删脚本/schema/npm script,usage.ts
  恢复 aef9887^ 手写版(字节级还原)。**类型对齐回归纯手写自律**:改后端响应模型时记得同步
  `frontend/src/lib/api/{usage,logs,models,config,data}.ts` 的同形 interface(round3 §5-S2
  风险记录仍有效)。若未来进入「频繁改响应模型 × 多消费方」阶段再评估引入,届时一次推完、不留中间态。
- **S3 CI / pre-commit**:`ruff format --check && ruff check && pyright && pytest -q` +
  前端 `oxlint && tsc -b`。已 2026-08-06 一次性 `ruff format`(74/85 重排)并纳入
  后端验收命令;CI/pre-commit 自动化仍未建(本地手动执行)。
- **S5 `useSyncedForm<T>` 抽象(已落地,2026-08-13)**:general/wol/claude-path/model-def-form
  四处手写「服务端快照→本地表单」同步已收敛为 `frontend/src/lib/hooks/use-synced-form.ts`
  (external-follow 仅在未编辑时;baseline 仅 onSuccess 推进;alwaysDirty 支持创建态)。
  单例语义/契约不变,后续新增表单一律用它。
- **🔵1 create_task 任务集**:6 处 fire-and-forget 任务内部均已捕异常,实际未检索异常风险低;
  跨模块引用集 helper 性价比不足。
- 其它:双账本(内存计数 + DB 落库)、`_migrate` 历史链退役窗口、前端 `useLogViewer` 改 useReducer
  + 虚拟化、时长格式化函数收敛——均纯清理,不影响正确性。
