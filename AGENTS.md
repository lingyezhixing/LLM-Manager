# AGENTS.md — LLM-Manager 协作契约

> 本地单一事实源:架构分层、不变量、命令、配置链路、退出码。代码注释里的
> `spec §x / invariant N / guard D` 历史计划档案会随文档演进漂移,**以本文件为准**。

## 1. 它是什么

本地多 LLM 模型的代理网关 + WebUI:按需启动/空闲回收本地模型进程(llama.cpp /
lmdeploy / vLLM …),对外暴露 OpenAI / Anthropic / Responses 兼容 API,记录用量与计费,
提供系统配置、模型管理、用量统计、日志查看的前端。**默认零配置零出网,无任何自动外呼**。
**运行时联网点 = 自更新 + 云服务商代理**:云服务商代理仅当「已配置且启用」的服务商收到对应
请求时才出网(见 §2 gateway 层与 README「云服务商配置」);自更新(系统页「更新」区,git
fetch/merge 本项目仓库)程序启动时自动检测一次,此后仅用户显式点击检查/应用按钮才联网;见 §5.1。
注意:**开发基础设施**(GitHub Actions CI,见 §6)是仓库侧的联网点,与运行时互不干扰
——上述联网点即产品运行时全部出网场景。

- 后端:Python 3 + FastAPI + uvicorn + SQLite(单连接 + `write_lock`)。`src/llm_manager/`
- 前端:React 19 + Vite + TS + Tailwind v4 + TanStack Query。`frontend/`
- 进程:单 Python 进程跑一个 app(见不变量 1)。8080 端口同时 serve API 与前端构建产物
  (`frontend/dist`,**非实时源码**——改前端后须 `npm run build` 或看 Vite dev 端口)。
  **dist 已入库**:改前端须重建并提交 dist,克隆即用、无需本地构建。

## 2. 架构分层(依赖单向无环)

```
config   ── 纯数据 + validate(DB → frozen dataclasses;设备名存储原样,匹配时归一化)
  ↓
state    ── 内存状态机(ModelStatus)+ 单派发 inflight Future + activity(无锁,单线程)
  ↓
supervisor ── 子进程管理(_procs/_exit_cbs/_readers 三表 + kill_tree + 单 _wait 协程)
  ↓
runtime  ── lifecycle(编排)/scheduling(纯函数资源决策)/background(心跳 30s + 日志保留 + 空闲回收 + 自启)/update(自更新:git 编排)
  ↓
data     ── persistence(schema + 旧库守护)/logs(会话/行 SQL + 捕获/广播/flush)/usage(计费 + 会话计数)/config_store(DB 配置)
  ↓
gateway  ── proxy(流式代理 + 用量计量 + 云端转发分支 gateway/cloud.py)+ api/*(REST/SSE 端点,含 tools_api)+ aliases(别名解析)
  ↓
tray     ── 系统托盘(自重启触发 / WOL / Claude 预设应用)
```

**分层纪律**:上层依赖下层,绝不反向。`scheduling.py` 纯函数(决策与副作用分离,
无 IO,单测无需 fake)。并发靠「单事件循环 + 临界段内无 await」保证(见不变量 2)。

**被多方引用的 leaf 模块(不在主链上,依赖亦单向)**:
- `devices/` —— 适配器协议化(DeviceAdapter + build_adapters 平台自动装配),被
  app / realtime / gateway(devices API)/ runtime(scheduling)引用。
- `tools/` —— WOL / Claude 预设纯逻辑,供 tray 与 gateway(tools_api)复用。
- `runner.py` —— parent 监督器入口(见 §5)。

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
   parent;`trigger_restart` 无 server 分支则延迟 `os._exit(81)`(dev 进程一次性,可接受)。
6. **运行中 = 内存 live 集,非 `end_time IS NULL`**。崩溃随进程消失,故残留会话/段天然
   落为 ended。心跳每 30s 把运行中项的 `end_time` 推到 now(只管时间,不管状态)。
   → **绝不能用 `end_time is not None` 判运行中**(曾致日志页运行中消失 bug)。
   日志会话 status = `CASE WHEN id IN live_session_ids() THEN 'running' ELSE 'ended'`。
7. **`aliases[0]` = 下游 served name**。模型 `aliases` 有序,首个即 lmdeploy `--model-name` /
   llama.cpp `-a` 的服务名;客户端请求按任意别名路由,但 served name 固定为 aliases[0]。
8. **DB 配置单一源**。运行时只读 DB 快照(`ConfigStore.snapshot()`,frozen)。env
   (`LLM_MANAGER_*`)在启动期写库(`apply_env_overrides`),不直接覆盖运行变量。
   **无 YAML 导入**:空库由 `initialize` seed 默认值(程序参数),模型经 WebUI CRUD
   添加——DB 完全接管。
9. **DB 只向前演进,旧库守护只拒不迁**。启动检测 Round-2 时代旧结构(`model_pricing` /
   `model_scripts` 表或 `model_requests.ts` 列)→ 明确报错(`LegacySchemaError`),不做任何
   自动迁移;新库 schema 即终态。v3.x 内升级无感(见 §9 发布说明通用声明),schema 变更
   必须向后兼容。

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
- **服务商 CRUD**(`/api/config/providers` POST/PUT/DELETE):同 `mutate_appconfig`,改名
  迁移经 post_write(与模型改名一致);validate 失败同样 →422,不落脏数据。
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
  `trigger_restart` 无 server 分支 `os._exit(81)`(dev 一次性)。
- `POST /api/config/restart`(WebUI 顶部重启横幅)走 `restart_requested → worker exit 81 → parent 拉新`;
  `POST /api/update/apply` 共用同一路径。
- `LLM-Manager.bat` 仅作 Windows 静默后台启动(VBS),不参与重启。

### 5.1 自更新(仅向前,双目标细粒度,严格 ff-only)

- **版本身份 = git 标签**(当前 = `git describe --tags --abbrev=0 HEAD`)。发版必须打标签。
- **更新目标两个细粒度**:`tag`(origin/main 最近可达标签,稳定发布)/ `commit`
  (origin/main 最新提交,前沿)。**无回退 / 无版本选择 / 无提交树浏览**——数据库结构
  只向前迁移,旧代码无法解读新 schema,故不支持回到旧版本。
- **检测语义**:程序(worker)启动时后台检测一次(fetch,不动工作树),结果缓存
  `app.state.update_status`;此后无任何自动检测。`GET /api/update/status`(读缓存,
  无网络;启动检测未完成 → checking=true,前端短轮询等待)/ `POST /api/update/check`
  (手动重新检测,唯一重新联网入口,前端「检查更新」按钮)/ `POST /api/update/apply`
  body `{"target": "tag"|"commit"}`(fetch + `git merge --ff-only <目标>` →
  `trigger_restart` → exit 81 → parent 拉新 worker。editable 安装下工作树即源码,
  新进程 import 即新代码)。
- **支持性门控**:git 未安装 / 非 git 仓库 → `supported=false`,前端隐藏更新功能
  (仅剩启动时间/运行时长)。
- **严格语义**:本地未提交改动不预拒——交给 git 原语,仅与更新内容冲突时拒绝(绝不
  stash/覆盖);本地历史分叉同样拒绝。均 409。
- **Docker/容器**:镜像缺 `openssh-client` 时,origin 若为 SSH URL → fetch 仅本次自动
  HTTPS 重写(`-c url.<https>.insteadOf=<ssh前缀>`,免认证拉公开仓库,不碰宿主推送配置)。
  root 容器下宿主仓库须为 root 属主,否则 git "dubious ownership" 拒绝 → 功能隐藏
  (这层拒绝是有意为之:root 写非 root 属主 bind-mount 会改宿主文件属主)。
- **网络纪律**:自更新的唯一联网点——程序启动时自动检测一次(worker 启动后台 fetch 一次),
  此后无任何自动联网,仅用户显式按钮触发(系统页「更新」区)。
- 注意:更新后依赖若变,editable 安装不会自动重装(pip 层自理)。

## 6. 命令(验收用)

后端(项目根,conda env 用开发环境 `LLM-Manager-Dev`):
```bash
python -m pytest tests -q          # 全量(含 smoke)
ruff format --check .             # 格式(改完须保持 format 干净)
ruff check .                     # lint(规则集 = ruff 0.16.1 默认全量,版本在 dev 依赖中 == 固定;
                                  #   升级版本即引入新规则,需显式处理;见 pyproject [tool.ruff])
pyright src/llm_manager            # 类型检查(0 errors 基线)
```
> **环境分工(本机)**:`LLM-Manager` env = 稳定版运行时,editable 安装指向部署目录
> `D:\LLM\LLM-Manager`,由 `LLM-Manager.bat` 启动 `python -m llm_manager`,**不含开发依赖**
> (无 pytest/ruff/pyright),勿用它跑上述命令。`LLM-Manager-Dev` env = 开发环境,editable 安装
> 指向本仓库(项目根),由 `Dev-Backend.bat` 启动 `uvicorn --factory --reload`,跑上述验收命令。
前端(`frontend/`):
```bash
npm run build        # = tsc -b && vite build;改前端后必跑(8080 serve dist)
npx oxlint src       # lint(存量 2 warning:toast/dialog 的 only-export-components,已知)
npx tsc -b           # 仅类型检查
npm test             # vitest run(核心纯逻辑:日志 reducer 状态机/块划分;14 例基线)
```

**CI(`.github/workflows/ci.yml`)**:push main / PR 触发,三 job 与上述命令同一套:
- backend(pip `-e .[dev]` + ruff format --check + ruff check + pyright + pytest)**双平台矩阵**
  ubuntu + windows——windows 侧真实覆盖 devices LHM 降级链与 runner 信号分支。
- frontend(npm ci + oxlint + vitest + tsc -b + build)ubuntu。

验收命令**本地也要跑一遍**(先本地后推送;CI 是兜底不是主力,别等 CI 才发现红)。
后端测试在双平台跑:测试不得写成 Windows-only 形状(字符串 spawn / 依赖本机 GPU /
直接引用 Windows 常量——2026-08-24 曾一次 12 红,全因测试假设了 Windows 环境)。
前端测试文件命名 `*.test.ts`,由 `.gitignore` 白名单(`!frontend/**/*.test.ts`)显式放行
——旧 `*test*` 规则会吞掉它们,新增测试文件前先确认白名单仍在。

## 7. 模块级单例(单进程前提 = 不变量 1)

| 模块 | 单例 | 说明 |
|---|---|---|
| `state` | `_state` / `_inflight` | 模型状态机 + 单派发 Future |
| `data.logs` | `_db` / `_sessions` / `_alias_to_session` / `_pending`(live.py)+ `_flush_chain`(pipeline.py) | 日志会话 live 集 + alias↔会话映射 + 待落库 + flush 串行链 |
| `data.usage` | `_live_segments`(record.py)/ `_c`(counters.py) | 运行中计费段(崩溃随进程消失)+ 进程内用量计数器(重启清零,概览 session-stats 卡) |
| `devices` | `_LHM_COMPUTER`(LibreHardwareMonitor) | Windows GPU/CPU 传感器单例;Linux Intel iGPU 走 i915 + intel_gpu_top 采样、AMD 走 amdgpu sysfs(均无单例) |
| `bgtask` | `_background` | fire-and-forget 任务强引用集合(asyncio 弱引用追踪有被 GC 理论风险);done 回调即时移除,不累积 |

测试接缝:state 有 `_reset()`、logs 有 `reset()`、usage 有 `_reset_counters()`(session 计数);
usage 的 `_live_segments` 由 `tests/unit/data/test_persistence.py` 的本地 fixture 直接清。
**新增模块级可变状态前先想清楚**:它隐式假设「整个进程只有一个 app 实例」,
破坏该假设会牵连 live 集语义。

## 8. 工作流约束

- **提交与推送纪律**:不要擅自频繁提交细碎的 commit——同一任务的修改合并为一次(或少量)
  提交,改一点就提交一点的习惯不要有;push 前必须征得用户许可,未经许可不推送。
  小任务直接在 `main`;较大特性开 feature 分支(非 worktree)FF-merge + 删分支。
- **CI 红灯不放行**:推送触发 CI(main);结果须全绿。红 = 已实锤回归,先修再推后续
  (自更新吃 main,红码会传播给 tag/commit 目标用户)。
- **完全离线**:图标(lucide 内联)、字体(系统字体)、资产严禁 CDN 引用。
- **改后端响应模型须同步前端类型**:`frontend/src/lib/api/{usage,logs,models,config,data,tools,update}.ts`
  的手写 interface 与后端 Pydantic 响应同形对齐(纯手写自律,无代码生成)。
- **新表单一律用 `useSyncedForm`**:`frontend/src/lib/hooks/use-synced-form.ts`(服务端快照→
  本地表单同步:external-follow 仅在未编辑时;baseline 仅 onSuccess 推进;alwaysDirty 支持创建态)。

## 9. 发布说明规范(GitHub Release)

写发布说明的原则与结构(仿 v2.7.0 平铺式):

**原则**
- **实用且平衡**:普通用户看得懂、开发者用得上;拒绝华而不实——无 emoji 标题、无「质变/可维护性质变」类营销词。
- **事实先核实**:每条变更必须能对应 git 提交;无法验证的修复/迁移/覆盖范围不写。跨大版本升级问题须对照真实 schema + 迁移链源码,必要时建库实测,写明「哪些能迁移、哪些不能、何时放弃旧代码/旧库」。
- **版本定位一句话放开头**:Alpha 用 `>` 引用显式标注;正式/维护版不加 `>` 注释。

**结构(仿 v2.7.0 平铺式)**
- 全部分类同级平铺(`###` 标题),**不分级、无包裹标题**(不写「主要变化」「工程(给开发者的)」);内部细节需要时用子要点。汇总型大版本介绍(如 v3.0.0)可升级为 `##` 层级分组,增量小版本用 `###` 平铺。
- 用户可见变更在前(每条写「对用户有什么用」),工程变更在后。
- 工程分类细分且正式:`架构优化 / 代码优化 / 性能优化 / 工程化`。
- **不写**:`测试` 小节(含测试数量/回归测试,全部不进说明)、`安装 / 运行`(README 的职责)。
- 升级注意(`### 升级注意`):只写可操作项(备份、断链行为、需重建什么)。
- **跨大版本的升级问题只在大版本首个正式版说明一次**(如 v2→v3 只在 v3.0.0 讲),后续版本不重复,并附通用兜底声明:
  「如无特别说明,任意 v3.x 版本均可无感升级至任意更高的 v3.x 版本(不保证降级)」。
- 设备/平台覆盖类描述用「理论覆盖 + 尚未全面实机验证」口径,不夸大实测范围。

**发布动作**
- 前置:当前 main 的最新 CI run **全绿**才 bump(自更新吃 main,红码会传播给 tag/commit
  目标用户)。
- 版本号 = git 标签(见 §5.1);Release 标题沿用 tag 名。
- 正文直接 `gh release edit <tag> --notes-file <file>` 覆写(经 GitHub API,非 git push、不受
  §8 推送许可限制;用户已确认允许)。
- 重大发布(如 v3.0.0)可汇总自上一大版本以来全部变化,以正式版规范写成完整介绍;小版本只写本版增量。

## 10. README 规范

README 是面向用户的文档式操作手册(与 §9 的 release 平铺式不同,`##` + `###` 层级结构),原则与结构:

**原则**
- **实用诚实、非营销**:不用「功能特性」宣传列表,不堆 bold 标签与「万行级平滑浏览 / 动效即反馈」类包装词;每条写「怎么用」而非「我们有什么」。
- **事实先核实**:与发布说明同律——页面分区、字段、默认值须对照源码;覆盖类描述用「理论覆盖 + 尚未全面实机验证」口径(如设备监控)。
- **职责分工**:**安装 / 运行 + 操作手册是 README 的职责**(release 不写,见 §9);架构 / 开发只作简介并指向 `AGENTS.md`;升级注意简短、只给结论并指向对应发布说明。

**结构(文档式,参考)**
- **快速开始前置**:环境要求 → 安装(`pip install -e .` + 可选 `[monitoring]`/`[tray]`/`[dev]`)→ 启动 → 添加第一个模型(真实示例命令 + `{{port}}` / `{{alias}}` 变量说明)→ 调用示例(curl)。
- 主体按操作手册组织:API 接口(表格)→ 配置(系统 / 模型 / 数据库分区 + 环境变量 + 重启规则)→ 设备监控 → 日志与数据 → 自更新 → 系统托盘 → 工具箱(WOL / Claude 预设)→ Docker 部署 → 升级注意 → 架构(简介)→ 开发(命令)。
- 配置类章节写「在哪改、字段含义、改后是否重启」;页面分区须对应 WebUI 当前实现(如系统页 3 zone、工具箱 2 zone),不沿用过时描述。
- 环境变量区分两类:`LLM_MANAGER_HOST/PORT/ALIVE_TIME/LOG_LEVEL` 覆写并持久化;`LLM_MANAGER_DB_PATH` 仅决定 DB 路径(不写入配置)。

**维护**
- 改 WebUI 页面结构、配置字段或默认值时,同步 README 相应小节;默认值须与 `PROGRAM_DEFAULTS` / `RETENTION_DEFAULTS` 一致(host `0.0.0.0:8080`、alive_time 60、日志保留 30 天 / 10 条)。