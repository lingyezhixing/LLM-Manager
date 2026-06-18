# Phase B 设计:多端点 token 解析器(parser_registry)

**日期**: 2026-06-19
**状态**: 待审核
**分支**: `refactor/probe-registry-phase-a`(继续,叠在 Phase A 之上)
**前置**: Phase A 完成(probe_registry、去 validate 闸门、track-all)。参考 `docs/superpowers/specs/2026-06-14-probe-registry-track-all-design.md` §8 预览。

---

## 1. 背景与动机

Phase A 完成了架构重构,但 `TokenTracker._extract_tokens`([api_router.py:23](core/api_router.py#L23))**一行没改** —— 它只认两种结构:llama.cpp 的 `timings`,或 OpenAI 的 `prompt_tokens`/`completion_tokens`。后果:

| API | 实测(10011 / Qwen3.5-9B) | Phase A 后提取结果 | 落库? |
|---|---|---|---|
| `/v1/chat/completions` | 有 `timings` + `usage`(prompt_tokens) | ✅ 正确(input=cache_n+prompt_n) | ✅ |
| `/v1/messages` | 无 timings;usage 用 `input_tokens`/`output_tokens`/`cache_read_input_tokens`;流式拆在 message_start + message_delta | ❌ (0,0,0,0) | ❌ 跳过 |
| `/v1/responses` | 无 timings;usage 用 `input_tokens`/`output_tokens`/`input_tokens_details.cached_tokens`;流式嵌在 `response.usage` | ❌ (0,0,0,0) | ❌ 跳过 |

两个失效根因(第 1-3 轮已用 10006 实测 + 官方文档交叉确认,本次 10011 复核一致):
1. **字段名**:Anthropic 与 Responses 用 `input_tokens`/`output_tokens`,代码只认 `prompt_tokens`/`completion_tokens`。
2. **结构**:messages 流式 usage 拆两事件(需合并);responses 流式 usage 嵌在 `data.response.usage`(需下钻)。当前"倒序取首个非零块"的扫描策略两者都处理不了。

## 2. 目标 / 非目标

### 目标
- 引入 path-keyed `parser_registry`,按请求 path 分派解析器。
- `/v1/chat/completions`、`/v1/completions`、`/v1/messages`、`/v1/responses` 四条路径,流式 + 非流式,token 全部正确提取落库。
- **不回归**:`/v1/embeddings` 当前靠 `prompt_tokens` 能抓输入 token,需保持(parse_openai 的 usage 分支天然覆盖)。

### 非目标
- 改计费公式([api_server.py:127-145](core/api_server.py#L127) 不动)、DB schema、webui。
- 改 analyzer 之外的 TokenTracker 职责(record_request_tokens 的全零守卫、异步落库、起止时间都不动)。
- `/v1/rerank` 的解析(rerank 响应 usage 非标准,且当前也未稳定追踪)→ 维持 no-op,后续单独处理。

## 3. 架构

### 3.1 新组件:`core/token_parsers.py`

纯解析模块(单一职责:从响应字节提取 4-tuple)。不含 DB、不含异步。

```python
# core/token_parsers.py
"""Path-keyed token parsers. 每个 parser: (body: bytes) -> (input, output, cache_n, prompt_n).
异常安全:任何错误返回 (0,0,0,0),绝不抛。"""
from typing import Tuple, Callable
import json, re, logging

logger = logging.getLogger(__name__)

def _is_sse(body_str: str) -> bool:
    return "data: " in body_str or body_str.lstrip().startswith("event:")

def _iter_data_blocks(body_str: str):
    """正序产出每个 'data: ' 后的 payload 字符串(SSE);非 SSE 则尝试整体 JSON / 正则提 JSON。"""
    ...

def parse_openai(body: bytes) -> Tuple[int, int, int, int]:
    """/v1/chat/completions + /v1/completions + /v1/embeddings。
    优先 timings(覆盖有 timings 的 chat/completions/completions);降级 usage(prompt_tokens/completion_tokens,覆盖 embeddings 与无 timings 场景)。"""
    ...

def parse_anthropic(body: bytes) -> Tuple[int, int, int, int]:
    """/v1/messages。SSE: 合并 message_start 的 input 类字段 与 最后一个 message_delta 的 output_tokens。
    非流式 JSON: 顶层 usage。无 timings。"""
    ...

def parse_responses(body: bytes) -> Tuple[int, int, int, int]:
    """/v1/responses。非流式 JSON: 顶层 usage。SSE: response.completed 事件里嵌套的 response.usage。无 timings。"""
    ...

def _parse_noop(body: bytes) -> Tuple[int, int, int, int]:
    return (0, 0, 0, 0)

parser_registry: dict[str, Callable] = {
    "v1/chat/completions": parse_openai,
    "v1/completions":      parse_openai,
    "v1/embeddings":       parse_openai,
    "v1/messages":         parse_anthropic,
    "v1/responses":        parse_responses,
}

def parse_tokens(path: str, body: bytes) -> Tuple[int, int, int, int]:
    """按 path 分派。未知 path -> _parse_noop(由调用方的全零守卫跳过)。"""
    key = path.lstrip("/").split("?")[0]   # 规范化:去前导斜杠、去 query
    return parser_registry.get(key, _parse_noop)(body)
```

> **异常安全契约**:每个 parser 顶层 `try/except` 包裹,任何异常(含 JSONDecodeError、KeyError、网络字节损坏)→ `logger.debug` + 返回 `(0,0,0,0)`。**绝不抛**——否则流式 `create_stream_with_token_logging` 的 `finally` 块里抛异常会截断已发给客户端的流。实现时用装饰器 `@_safe` 强制,或每个 parser 内部 try/except;测试用损坏 fixture 验证不抛。

### 3.2 改 `TokenTracker`([api_router.py](core/api_router.py))

- `extract_tokens_from_response(self, content, path)` —— **加 `path` 参数**,内部改为 `from core.token_parsers import parse_tokens; return parse_tokens(path, content)`。保留原有的 debug 日志(末尾数据块打印)作为辅助。
- `_extract_tokens(self, data)` —— **保留**,被 `parse_openai` 复用(timings/单块 usage 提取逻辑搬进 `parse_openai`,或 `parse_openai` 调用它处理单块)。避免逻辑重复。
- `create_stream_with_token_logging(self, model_name, response, request_start_time, path)` —— **加 `path` 参数**,`finally` 里 `extract_tokens_from_response(full_content, path)`。

### 3.3 改 `route_request`([api_router.py](core/api_router.py))

它已经持有 `path`,只需向下传:
- 非流式分支(~390):`token_tracker.extract_tokens_from_response(content, path)`。
- 流式分支(~365):`token_tracker.create_stream_with_token_logging(model_name, response, request_start_time, path)`。

## 4. 四元组映射(关键,决定计费对错)

DB 存 `(input_tokens, output_tokens, cache_n, prompt_n)`;计费公式 [api_server.py:137-142](core/api_server.py#L137) 用 `cache_n`(缓存读,便宜)和 `prompt_n`(未命中,含写缓存,贵)分开算。各 parser 必须产出:

| parser | input_tokens(DB 显示总量) | output_tokens | cache_n | prompt_n(未命中,计费用) |
|---|---|---|---|---|
| **parse_openai**(timings 分支) | `cache_n+prompt_n` | `predicted_n` | `timings.cache_n` | `timings.prompt_n` |
| **parse_openai**(usage 降级) | `prompt_tokens` | `completion_tokens` | `prompt_tokens_details.cached_tokens` | `prompt_tokens - cached_tokens` |
| **parse_anthropic** | `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` | `output_tokens` | `cache_read_input_tokens` | `input_tokens + cache_creation_input_tokens` |
| **parse_responses** | `input_tokens`(已是总量) | `output_tokens` | `input_tokens_details.cached_tokens` | `input_tokens - cached_tokens` |

**关键非对称**(第 3 轮官方文档坐实):
- Anthropic 的 `input_tokens` 字段是**非缓存基准**(不能再减;`cache_read_input_tokens` 是独立加项)。
- OpenAI chat usage 与 Responses 的 `input_tokens`/`prompt_tokens` 是**总量含缓存**(必须减 `cached_tokens`)。
- 若用统一公式套所有格式 → 计费腐蚀。所以映射必须 per-parser 写死,测试钉死。

实测自洽(10011,缓存暖后):chat `prompt_n=19`(冷);messages `input_tokens=4 + cache_read=15`;responses `input_tokens=19 - cached=15 → 非缓存 4`。三者的 cache_n=15、prompt_n(非缓存)=4 应当一致。

## 5. 扫描策略(per parser)

| parser | 非流式(JSON) | 流式(SSE) |
|---|---|---|
| parse_openai | 整体 JSON;`timings` 优先,降级 `usage` | **倒序**扫 `data:` 块,首个含 timings(或 usage)的块即返回(与现状一致;chat/completions 的 timings 在最后一个 finish_reason 块里) |
| parse_anthropic | 整体 JSON;读顶层 `usage`(input_tokens / cache_read_input_tokens / cache_creation_input_tokens / output_tokens) | **正序**扫:找 `message_start` 取 input 类字段,再走到**最后一个** `message_delta` 取 `output_tokens`,合并。**不能用倒序取首块**(会先撞 message_delta 丢 input) |
| parse_responses | 整体 JSON;读顶层 `usage` | 找 `response.completed` 事件,读**嵌套** `data["response"]["usage"]`(顶层无 `usage`)。单块,但要下钻一层 |

## 6. 异常安全

每个 parser **绝不抛**:
- 顶层 try/except,任何异常 → `logger.debug(异常信息)` + 返回 `(0,0,0,0)`。
- 用装饰器 `@_safe` 统一强制(推荐),或每个 parser 内部 try/except。
- 测试:用截断/损坏的 SSE fixture 喂每个 parser,断言返回 `(0,0,0,0)` 且不抛。
- 理由:流式 `finally` 块里抛异常会中断已部分发给客户端的响应。

## 7. 测试

**fixture-based(确定性、快)**:从 10011 抓取每种响应的真实字节,存为 `tests/fixtures/*.txt`,parser 测试读 fixture 断言 4-tuple。
- `parse_openai`:chat/completions 流式 + 非流式;completions 非流式;embeddings 非流式。
- `parse_anthropic`:messages 流式(合并 message_start + message_delta)+ 非流式。
- `parse_responses`:responses 流式(嵌套 response.usage)+ 非流式。
- 断言:`cache_n=15, prompt_n=4, output_tokens=30`(10011 实测值,缓存暖后)三端一致;input_tokens 总量对齐。
- 异常安全:每个 parser 喂损坏/截断 fixture → `(0,0,0,0)` 不抛。
- dispatcher:`parse_tokens("v1/messages", ...)` 路由到 parse_anthropic;未知 path → `(0,0,0,0)`。

**live 集成(手动,Phase B 末尾)**:经 manager(`127.0.0.1:8080`)分别打 /v1/chat/completions、/v1/messages、/v1/responses,确认 `webui/monitoring.db` 三条新记录的 `cache_n=15, prompt_n=4`(或当次实测值)非零且一致。

## 8. 不动的部分

- 计费公式、DB schema、webui、probe_registry、validate 闸门(已无)、track-all、分析维度。Phase B 只动 token 解析 + dispatch 接线。

## 9. 回滚

改动集中在:新 `core/token_parsers.py` + `TokenTracker` 三个方法签名加 path + `route_request` 两处传 path。纯增量 + 小改,无 DB 迁移。回滚 = git revert。
