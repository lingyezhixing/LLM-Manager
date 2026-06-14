# Phase A 设计:probe_registry + 去 validate 闸门 + track-all

**日期**: 2026-06-14
**状态**: 已批准,待实现
**范围**: 路径分派重构的 **Phase A**(架构简化)。Phase B(多端点 token 解析器)是**独立后续 spec**,不在本文件范围内。

---

## 1. 背景与动机

当前系统用一个四层结构把"接口类型"绑在一起:

1. `InterfacePlugin` ABC(`plugins/interfaces/Base_Class.py`)+ 四个子类(chat/base/embedding/reranker),每个定义 `health_check` / `get_supported_endpoints` / `validate_request`。
2. `InterfacePluginLoader` + `PluginManager` 的 interface 管道(`core/plugin_system.py`),按 `interface_name` 动态发现加载。
3. `ModelController._perform_health_checks` 按模型 `mode` 取一个插件,调 `.health_check()` —— **这是要保留的冷启动耦合**。
4. `APIRouter.route_request` 按模型 `mode` 取插件,调 `.validate_request()` 拦截"不匹配"的路径 —— **这是要删除的请求闸门**。

这套结构把两个**正交的关注点**绑死在一个类里:

- **健康探测**(每模型一个,冷启动时选):按 **mode** 区分(chat 探测 / embedding 探测 / rerank 探测)。
- **token 解析**(每请求一个,转发时选):按 **path** 区分(`/v1/chat/completions`、`/v1/messages`、`/v1/responses`…)。

绑在一起导致:新增一个 API(如 `/v1/responses`)要同时改接口插件的端点集合、validate 规则、token 解析;配置里还要给每个模型指定 `mode` 和白名单才能追踪。

**Phase A 的目标**:拆开第一个关注点(健康探测)并砍掉闸门和白名单,把架构简化为干净的 `probe_registry`。token 解析的拆分(parser_registry)留给 Phase B。

## 2. 目标 / 非目标

### 目标
- 用 `probe_registry`(mode → 探测函数)替换整个 `InterfacePlugin` 类体系。
- 删除 `APIRouter.route_request` 的 validate 闸门:**全部路径转发**。
- 删除 `program.TokenTracker` 白名单:**全部转发的 LLM 流量都追踪**(track-all)。
- 保留每模型 `mode` 字段,语义收窄为:**健康探测选择 + 分析看板分组维度**。mode 取值不变(Chat/Base/Embedding/Reranker)。

### 非目标(留给 Phase B)
- `/v1/messages`、`/v1/responses` 等 token 漏抓的**修复**。Phase A 完成后,这些路径仍然抓不到 token(extractor 返回 0,被跳过)。修复在 Phase B 的 parser_registry。
- 改 token extractor 的扫描策略(Anthropic 合并 / Responses 嵌套 usage)。
- 改 webui、改 DB schema、改计费公式。

### 已确认无影响(经工作流验证)
- **webui 零改动**:前端 `mode_breakdown` 是通用 `Record<string, ...>`,`Object.keys/entries/values` 动态迭代;唯一硬编码 mode 名的地方是 `ModeThroughputChart.tsx:47-56` 的颜色 map,带 `Default` 兜底。
- **DB 零迁移**:无 schema 变化;`_get_model_id` 自动建行,`add_model_request` 接受任意配置过的模型名。

## 3. 架构

### 3.1 新组件:`core/probes.py`

把四个 interface 插件的 `health_check` 函数体**原样搬**成普通函数,加一个模块级注册表。

```python
# core/probes.py
from typing import Tuple
import openai, time, logging

logger = logging.getLogger(__name__)

# 签名契约(钉死):probe_fn(model_alias, port, start_time, timeout) -> (bool, str)
# 位置参数,与现状 health_check 完全一致。违反会静默破坏 300s 加载等待保证。

def probe_chat(model_alias, port, start_time=None, timeout=300) -> Tuple[bool, str]: ...
def probe_base(model_alias, port, start_time=None, timeout=300) -> Tuple[bool, str]: ...
def probe_embedding(model_alias, port, start_time=None, timeout=300) -> Tuple[bool, str]: ...
def probe_reranker(model_alias, port, start_time=None, timeout=300) -> Tuple[bool, str]: ...

probe_registry = {
    "Chat": probe_chat,
    "Base": probe_base,
    "Embedding": probe_embedding,
    "Reranker": probe_reranker,
}
```

函数体分别来自 `plugins/interfaces/chat.py:17-61`、`base.py:17-61`、`embedding.py:17-60`、`reranker.py:17-65` 的 `health_check`,逻辑不变(浅层 `/v1/models` + 深层探测 + 重试循环)。

### 3.2 删除:`plugins/interfaces/` 整个目录

`Base_Class.py`、`base.py`、`chat.py`、`embedding.py`、`reranker.py`、`__init__.py` 全删。

> **纠错备注**:早期分析曾怀疑这些插件有 import bug、系统半残。实测纠正——`base.py:5` 重导出了 `InterfacePlugin`,`chat.py/embedding.py/reranker.py` 的导入链正常。这些插件**现在是活的**:`validate_request` 在 [api_router.py:277](core/api_router.py#L277) 确实在拦截跨模式请求。删闸门 = 删**活的**拦截逻辑,见 §5 行为变更。

### 3.3 改 `core/plugin_system.py`

| 符号 | 行 | 动作 |
|---|---|---|
| `InterfacePluginLoader`(类) | 170-189 | **删**。动态发现加载器,被 `probe_registry` 取代。 |
| `PluginManager.__init__` | 194-200 | **改**:去掉 `interface_dir` 参数、`self.interface_dir`、`self.interface_loader`、`self.interface_plugins`。构造器签名收窄为 `PluginManager(device_dir=...)`。 |
| `load_all_plugins` | 328-376 | **改**:删 interface 分支(设 `model_manager` + 调 `interface_loader.load_plugins`);返回 dict 去掉 `interface_plugins` 键(无调用方读它)。 |
| `reload_plugins` | 378-383 | **改**:删 `self.interface_plugins.clear()`。 |
| `get_interface_plugin` | 389-391 | **改名为 `get_probe(mode)`**,内部 `return probe_registry.get(mode)`。 |
| `get_all_interface_plugins` | 397-399 | **删**。唯一调用方是日志行(model_controller.py:393),一并改。 |
| `get_plugin_status` | 401-425 | **改**:删 `interface_plugins` 键块(死代码,零调用,不删会引用已不存在的属性)。 |
| `discover_new_plugins` | 427-450 | **改**:删 `interface_dir` 扫描 + `interface_plugins` 键(死代码)。 |
| `get_interface_loader` | ~514 | **删**。零调用。 |
| `PluginLoader`(基类) | 17-155 | **保留**。设备专用。其中 114-116 的 `model_manager` 注入分支变死代码(无害),实现时可顺手简化,非必须。 |
| `DevicePluginLoader` | 157-168 | **保留**。 |

### 3.4 改 `core/model_controller.py`

| 符号 | 行 | 动作 |
|---|---|---|
| `load_plugins` | 385-402 | **改**:删 388 `interface_dir = ...`;389 改为 `PluginManager(device_dir)`(签名已收窄);393 日志去掉"接口插件 N 个"(或换成 probe_registry 大小)。392 的 `model_manager=self` 传参变 inert(只有 InterfacePluginLoader 用过),保留无害或删。 |
| `_perform_health_checks` | 788-829 | **改(冷启动耦合,务必保留语义)**:800-801 `plugin = get_interface_plugin(mode)` → `probe_fn = self.plugin_manager.get_probe(mode)`;803-806 None 守卫保留(报错文案改成"未找到模式 '{mode}' 的健康探测器");813 `plugin.health_check(primary_name, port, start_ts, timeout)` → `probe_fn(primary_name, port, start_ts, timeout)`。**819-823 的 ROUTING 翻转、checkpoint 逻辑全部不动。** |

### 3.5 改 `core/api_router.py`

| 符号 | 行 | 动作 |
|---|---|---|
| `route_request` validate 闸门 | 272-279 | **删整块**:取 `model_mode`、取 `interface_plugin`、None 守卫、`validate_request`、is_valid 守卫。保留 [268-270](core/api_router.py#L268) 的 `get_model_config`(下游 `port` 要用)。 |
| token 提取/记录 | 363-404 | **不动**(Phase B 才改解析)。 |

### 3.6 track-all:`config_manager` + 9 处调用点

**新增** `ConfigManager.get_all_model_modes() -> List[str]`:返回 `sorted({cfg.get("mode","Chat") for cfg in get_all_model_configs().values()})`。

**删** `should_track_tokens_for_mode`(207-210)和 `get_token_tracker_modes`(203-205)。**严格在下面 9 处迁移完成后再删方法,否则 AttributeError 崩。**

| 文件 | 行 | 现状 | 改为 |
|---|---|---|---|
| `api_router.py` | 21 | init 日志读 `get_token_tracker_modes()` | 改文案为"tracking all forwarded traffic",去掉该调用 |
| `api_router.py` | 134-136 | `record_request_tokens` 的 `should_track_tokens_for_mode` 守卫 | **删守卫**(保留 138-140 的全零守卫)。可选保留 model_mode 仅用于 153 成功日志 |
| `api_server.py` | 169-170 | `get_model_requests_with_mode` 的 `should_track_tokens_for_mode` 早返回 | **删**,所有模型都查库;保留 168 取 mode、176 注入 model_mode 列 |
| `api_server.py` | 184-187 | `model_names_to_track` 列表推导过滤 | **改为** `self.config_manager.get_model_names()`(查全部) |
| `api_server.py` | 224 | `_calculate_hourly_cost_trends` 建 `mode_costs_per_bucket` 键 | `tracked_modes = get_all_model_modes()` |
| `api_server.py` | 510 | `get_throughput` 建键 | `get_all_model_modes()` |
| `api_server.py` | 654 | `get_usage_summary` 建键 | `get_all_model_modes()` |
| `api_server.py` | 708 | `get_token_trends` 建键 | `get_all_model_modes()` |
| `api_server.py` | 768 | `get_cost_trends` 建键 | `get_all_model_modes()` |

各 endpoint 的 `groupby(['bin_index','model_mode'])` / `groupby('model_mode')` **全部不动**(model_mode 列仍由 168/176 注入)。

### 3.7 配置与校验

- `config.yaml`:删 `program.TokenTracker` 块(改完后无人读)。**每模型 `mode` 字段保留**。
- `validate_config`(config_manager.py:239)**加一条**:每个模型的 mode 必须在 `probe_registry` 的 key 集合内,否则报配置错误并列出支持的 mode(避免 mode 拼错时冷启动给出费解错误)。

## 4. 契约(实现时钉死)

1. **探测签名**:`probe_fn(model_alias: str, port: int, start_time: float, timeout: int) -> Tuple[bool, str]`,位置参数,与现状 `health_check` 完全一致。违反会静默破坏 300s 加载等待(详见 §7 测试)。
2. **mode 取值**:固定 `{Chat, Base, Embedding, Reranker}`,与 `probe_registry` key 一一对应。
3. **顺序约束**:9 处白名单调用迁移完成 → 才能删 `should_track_tokens_for_mode` / `get_token_tracker_modes`;plugin_system 改完 → 才能删 `plugins/interfaces/`;model_controller 调用点改完 → 才能改 PluginManager 签名。

## 5. 行为变更(记入发布说明)

1. **跨模式请求不再被本地拦截**:给 reranker/embedding-only 模型发 chat 或 messages 请求,以前 [validate_request](core/api_router.py#L277) 直接返回干净 400,现在**转发到上游**、由上游报错(404/500)。符合"全部转发"的意图,但客户端看到的错误信息变了。
2. **分析看板数字会变**:`overall_summary` 总计和 `mode_breakdown` 桶现在包含**所有 mode 的流量**(以前 TokenTracker 白名单外的 mode 被排除)。这是 track-all 的本意。
3. **未知 path 的处理**:catch-all 路由([api_server.py:1160](core/api_server.py#L1160))仍转发任意 POST。Phase A 下,非 LLM 路径的响应经现有 extractor 返回 `(0,0,0,0)` → 被全零守卫跳过,**不污染 token 统计**(无害)。Phase B 才引入显式的 parser_registry no-op。

## 6. 数据流(Phase A 完成后)

- **冷启动**:`model mode` → `probe_registry[mode]` → 探测 → 成功则翻 `status=routing`(保留 300s 加载等待)。
- **请求转发**:解析 body 的 `model` → 解析模型 → (无闸门)→ 转发到模型端口 → 流式/非流式响应经**现有 path-agnostic extractor** → `record_request_tokens`(track-all,无 mode 过滤)→ 落库。
- **分析**:查全部模型请求 → 注入 model_mode 列 → 按 model_mode groupby → 返回 mode_breakdown。

## 7. 测试

1. **探测签名断言**:`inspect.signature` 检查四个 probe 函数参数为 `(model_alias, port, start_time, timeout)`。
2. **探测超时不秒返**:对不可达端口,probe 在 timeout 后返回 `(False, ...)`,而非立即 `(True, ...)`(防签名错位导致静默通过)。
3. **四种 mode 冷启动**:每个 mode 的模型走完 `_perform_health_checks` → `ROUTING`。
4. **track-all**:每个 mode 模型的请求都落库(查 `model_requests` 有记录)。
5. **9 个分析端点不崩**:`throughput` / `token-trends` / `cost-trends` / `usage-summary` / `current-session` / `model-stats` / `hourly-cost` 各发一次请求,返回 200 且 mode_breakdown 键来自 distinct 模型 mode。
6. **mode 校验**:config 里把某模型 mode 改成非法值(如 `Caht`),启动报配置错误。

## 8. Phase B 预览(本 spec **不含**,后续单独开)

- 引入 `parser_registry`(path → 解析器),dispatch 进 route_request 的流式([361-380](core/api_router.py#L361))和非流式([389-398](core/api_router.py#L389))分支。
- 填充格式专用解析器:`parse_anthropic`(`/v1/messages`,正序扫,合并 `message_start` 的 input+cache_read+cache_creation 与最后一个 `message_delta` 的 output)、`parse_responses`(`/v1/responses`,读 `response.completed` 里嵌套的 `response.usage`)、`parse_embedding`、`parse_reranker`;`parse_openai` 留给 `/v1/chat/completions` + `/v1/completions`;未知 path → no-op。
- 钉死每个解析器的四元组 `(input_tokens, output_tokens, cache_n, prompt_n)` 输出映射(Anthropic 的 input_tokens 是非缓存基准,与 OpenAI 的总量含缓存语义相反):
  - openai:`cache_n = prompt_tokens_details.cached_tokens`,`prompt_n = prompt_tokens - cached_tokens`
  - anthropic:`cache_n = cache_read_input_tokens`,`prompt_n = input_tokens + cache_creation_input_tokens`
  - responses:`cache_n = input_tokens_details.cached_tokens`,`prompt_n = input_tokens - cached_tokens`
- parser **异常安全**:任何错误返回 `(0,0,0,0)`,绝不抛(否则流式 finally 里抛异常会截断已发给客户端的流)。用装饰器或契约 + 模糊测试强制。
- 用 127.0.0.1:10006 的 llama.cpp 实测各端点流式/非流式,核对落库的 `cache_n=16, prompt_n=4`(缓存命中数)三端点一致。

## 9. 回滚

Phase A 改动集中在 6 个文件 + 删一个目录,均为可逆的代码删除/搬迁,无 DB 迁移、无配置破坏(mode 字段保留)。回滚 = git revert。
