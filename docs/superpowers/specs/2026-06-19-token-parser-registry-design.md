# Phase B 设计:多端点 token 解析器(parser_registry)

**日期**: 2026-06-19(基于 llama.cpp + lmdeploy 双后端全量实测重写)
**状态**: 待审核
**分支**: `refactor/probe-registry-phase-a`(继续,叠在 Phase A 之上)
**前置**: Phase A 完成。参考 `docs/superpowers/specs/2026-06-14-probe-registry-track-all-design.md`。

---

## 1. 背景与动机

Phase A 完成了架构重构,但 `TokenTracker._extract_tokens` 一行没改 —— 只认 llama.cpp 的 `timings` 或 OpenAI 的 `prompt_tokens`/`completion_tokens`。后果:`/v1/messages`、`/v1/responses` 转发了但 token 全提取为 0,被全零守卫跳过;且解析器没考虑 lmdeploy(无 timings、流式默认无 usage)。

本次设计经 **llama.cpp + lmdeploy 双后端、6 个类别(chat/embedding/rerank)全量实测** 验证(见 §2),所有字段映射对照真实响应逐条核对。

## 2. 跨后端格式矩阵(全部实测,2026-06-19)

测试服务:llama.cpp chat(Qwen3.5-9B @10011)、embedding(bge-m3 @10002)、rerank(bge-reranker-v2-m3 @10003);lmdeploy chat(Qwen3-4B-Instruct-2507 @10001)、embedding(Qwen3-Embedding-4B @10002)、rerank(Qwen3-Reranker-0.6B @10003)。真实响应存 `tests/fixtures/`(llama.cpp chat 8 个已存;其余实现时补)。

**Chat 类:**
| 路径 × 模式 | llama.cpp | lmdeploy |
|---|---|---|
| chat/completions 非流式 | `timings`(cache_n/prompt_n/predicted_n)+ `usage`(prompt_tokens/completion_tokens + prompt_tokens_details.cached_tokens) | `usage`(prompt_tokens/completion_tokens);**无 timings、无 cache** |
| chat/completions 流式 | 末个 finish_reason 块带 `timings`(流里**无 usage**) | **仅当 `stream_options.include_usage:true`** 时末尾 `choices:[]`+`usage` 块;否则无任何 token 信息 |
| completions 非流式 | timings + usage(同上) | usage(无 timings/cache) |
| completions 流式 | 末块带 timings + usage | 需 include_usage 才有 usage |
| messages 非流式 | `usage`:input_tokens/**cache_read_input_tokens**/output_tokens(input_tokens 是**非缓存基准**) | **不支持(HTTP 500)** |
| messages 流式 | usage 拆在 `message_start`(input/cache_read,output=0)+ 末个 `message_delta`(output_tokens);无 timings | 不支持 |
| responses 非流式 | `usage`:input_tokens(**总量**)/output_tokens/input_tokens_details.cached_tokens;无 timings | 一致(cached_tokens 恒 0,多 per-turn 细分) |
| responses 流式 | `response.completed` 事件,usage 嵌在 `data.response.usage` | **`response.completed` 或 `response.incomplete`**(截断时后者),都带 `response.usage` |

**Embedding / Rerank:**
| | llama.cpp | lmdeploy |
|---|---|---|
| embedding usage | `{prompt_tokens, total_tokens}`(无 completion/cache/timings;不可流式) | `{prompt_tokens, total_tokens, completion_tokens:0}`(多一个恒 0 的 completion_tokens) |
| rerank usage | `{prompt_tokens, total_tokens}`;**`/v1/rerank` 和 `/rerank` 都认** | `{prompt_tokens, total_tokens, completion_tokens:0}`;**仅 `/v1/rerank`**(`/rerank` → 404) |

**关键跨后端差异(驱动设计的 3 点):**
1. **lmdeploy 无 timings** → parse_openai 必须能纯靠 usage 走通(已满足)。
2. **lmdeploy 流式(chat/completions、completions)默认无 usage** → manager 需注入 `stream_options.include_usage:true`(§3.4),否则 lmdeploy 流式抓不到 token。
3. **lmdeploy responses 流式终止事件可能是 `response.incomplete`** → parse_responses 不能写死 `response.completed`,要认"带 `response.usage` 的终止事件"。
4. **lmdeploy 不支持 /v1/messages** → parse_anthropic 仅在 llama.cpp 生效(不影响设计,记录即可)。
5. **lmdeploy 无 cache** → cache_n 恒 0;`.get` 兜底已覆盖。

## 3. 架构

### 3.1 新组件:`core/token_parsers.py`(纯解析,单一职责)

```python
"""Path-keyed token parsers. 每个 parser: (body: bytes) -> (input, output, cache_n, prompt_n).
异常安全:任何错误返回 (0,0,0,0),绝不抛(流式 finally 里抛会截断客户端流)。"""
from typing import Tuple, Callable
import json, logging

logger = logging.getLogger(__name__)

def _safe(parser):
    """装饰器:保证 parser 绝不抛。"""
    def wrapped(body: bytes) -> Tuple[int, int, int, int]:
        try:
            return parser(body)
        except Exception as e:
            logger.debug(f"[parser] {parser.__name__} 提取失败: {e}")
            return (0, 0, 0, 0)
    return wrapped

@_safe
def parse_openai(body: bytes) -> Tuple[int, int, int, int]:
    """/v1/chat/completions + /v1/completions + /v1/embeddings + /v1/rerank + /rerank。
    优先 timings(llama.cpp chat/completions/completions);降级 usage(llama.cpp embeddings/rerank、lmdeploy 全部)。
    usage 的 prompt_tokens/input_tokens 是【总量含缓存】→ prompt_n = total - cached_tokens。"""
    ...

@_safe
def parse_anthropic(body: bytes) -> Tuple[int, int, int, int]:
    """/v1/messages(仅 llama.cpp)。无 timings。input_tokens 是【非缓存基准】
    → cache_n=cache_read_input_tokens, prompt_n=input_tokens+cache_creation_input_tokens(.get 默认 0)。
    流式:正序合并 message_start(input/cache 类) + 末个 message_delta(output_tokens)。"""
    ...

@_safe
def parse_responses(body: bytes) -> Tuple[int, int, int, int]:
    """/v1/responses(两后端)。无 timings。input_tokens 是【总量含缓存】。
    非流式:顶层 usage。流式:找带 response.usage 的终止事件(response.completed 或 .incomplete),下钻 data['response']['usage']。"""
    ...

def _parse_noop(body: bytes) -> Tuple[int, int, int, int]:
    return (0, 0, 0, 0)

parser_registry: dict[str, Callable] = {
    "v1/chat/completions": parse_openai,
    "v1/completions":      parse_openai,
    "v1/embeddings":       parse_openai,
    "v1/rerank":           parse_openai,
    "rerank":              parse_openai,   # llama.cpp 也认裸路径
    "v1/messages":         parse_anthropic,
    "v1/responses":        parse_responses,
}

def parse_tokens(path: str, body: bytes) -> Tuple[int, int, int, int]:
    """按 path 分派。未知 path -> _parse_noop。"""
    key = path.lstrip("/").split("?")[0]
    return parser_registry.get(key, _parse_noop)(body)
```

### 3.2 改 `TokenTracker`([api_router.py](core/api_router.py))
- `extract_tokens_from_response(self, content, path)` —— 加 `path` 参数,内部 `from core.token_parsers import parse_tokens; return parse_tokens(path, content)`。保留原 debug 日志(末尾块打印)辅助排障。
- `_extract_tokens(self, data)` —— 保留供 `parse_openai` 复用单块 timings/usage 提取(避免重复)。
- `create_stream_with_token_logging(self, model_name, response, request_start_time, path)` —— 加 `path`,`finally` 里 `extract_tokens_from_response(full_content, path)`。

### 3.3 改 `route_request`([api_router.py](core/api_router.py))—— 传 path
- 非流式分支(~390):`token_tracker.extract_tokens_from_response(content, path)`。
- 流式分支(~365):`token_tracker.create_stream_with_token_logging(model_name, response, request_start_time, path)`。

### 3.4 流式注入 `include_usage`(新,跨后端关键)
lmdeploy 流式(chat/completions、completions)默认不带 usage。在 `route_request` 读 body 后、重序列化前,**仅对这两个路径**注入:
```python
if body.get("stream") is True and path.lstrip("/").split("?")[0] in ("v1/chat/completions", "v1/completions"):
    so = body.get("stream_options") or {}
    so.setdefault("include_usage", True)   # 不覆盖客户端已有的其他选项
    body["stream_options"] = so
```
- 对 llama.cpp 无害(它本就有 timings);对 lmdeploy 是**必需**。
- 客户端会多收到一个 `choices:[]`+usage 的末块(OpenAI 标准块,主流客户端忽略额外数据)。
- 只对 chat/completions + completions 注入;responses/messages 流式本就带 usage,不需注入,且避免给它们塞不识别字段。

## 4. 四元组映射(关键,决定计费对错)

DB 存 `(input_tokens, output_tokens, cache_n, prompt_n)`;计费 [api_server.py:137-145](core/api_server.py#L137) 用 `cache_n`(缓存读,便宜)和 `prompt_n`(未命中,含写缓存,贵)分开算。

| parser(场景) | input_tokens(DB 总量) | output_tokens | cache_n | prompt_n(未命中,计费用) |
|---|---|---|---|---|
| parse_openai · timings 分支(llama.cpp chat/completions/completions) | `cache_n+prompt_n` | `predicted_n` | `timings.cache_n` | `timings.prompt_n` |
| parse_openai · usage 分支(llama.cpp embeddings/rerank、lmdeploy 全部、无 timings 时) | `prompt_tokens` | `completion_tokens`(.get 默认 0) | `prompt_tokens_details.cached_tokens`(.get 默认 0) | `prompt_tokens - cached_tokens` |
| parse_anthropic · messages | `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` | `output_tokens` | `cache_read_input_tokens` | `input_tokens + cache_creation_input_tokens`(.get 默认 0) |
| parse_responses · responses | `input_tokens`(已是总量) | `output_tokens` | `input_tokens_details.cached_tokens`(.get 默认 0) | `input_tokens - cached_tokens` |

**关键非对称(第 3 轮官方文档 + 本次双后端实测坐实):**
- **Anthropic** 的 `input_tokens` 是**非缓存基准**(不能再减;`cache_read_input_tokens` 是独立加项)。
- **OpenAI 系**(chat usage、Responses、embeddings、rerank)的 `prompt_tokens`/`input_tokens` 是**总量含缓存**(必须减 `cached_tokens`)。
- 若用统一公式套所有格式 → 计费腐蚀。映射必须 per-parser 写死,测试钉死。

**实测自洽(缓存暖后)**:llama.cpp chat timings cache_n=15/prompt_n=4;messages input_tokens=4+cache_read=15;responses input_tokens=19-cached=15→非缓存4。三者 cache_n=15、prompt_n=4 一致。

**`.get` 兜底要点**:embeddings/rerank 的 usage **没有** `prompt_tokens_details` 子对象(llama.cpp)或有但 cached_tokens=0(lmdeploy);messages **没有** `cache_creation_input_tokens`(llama.cpp)。所有缓存字段一律 `.get(field, 0)`,绝不假设存在。

## 5. 扫描策略(per parser)

| parser | 非流式(JSON) | 流式(SSE) |
|---|---|---|
| parse_openai | 整体 JSON;timings 优先,降级 usage | **倒序**扫 `data:` 块,首个含 timings(或 usage)的块即返回(llama.cpp:末 finish_reason 块带 timings;lmdeploy:注入 include_usage 后末块 choices:[]+usage)。chat 流式 llama.cpp **只有 timings 无 usage** → timings 分支是唯一通路 |
| parse_anthropic | 整体 JSON;顶层 usage | **正序**扫:取 `message_start` 的 input/cache_read/cache_creation + **末个** `message_delta` 的 output_tokens,合并。**不能用倒序取首块**(会先撞 message_delta 丢 input) |
| parse_responses | 整体 JSON;顶层 usage | 找带 `response.usage` 的**终止事件**(`response.completed` 或 `response.incomplete`),下钻 `data['response']['usage']`。**对 data: 块读顶层 usage 会失败**(嵌套一层) |

## 6. 异常安全

每个 parser 用 `@_safe` 装饰器统一 try/except,任何异常(JSONDecodeError、KeyError、字节损坏、截断)→ `logger.debug` + 返回 `(0,0,0,0)`,**绝不抛**。理由:流式 `create_stream_with_token_logging` 的 `finally` 块里抛异常会中断已部分发给客户端的响应。测试用损坏/截断 fixture 喂每个 parser 断言不抛。

## 7. 测试

**fixture-based(确定性)**:`tests/fixtures/` 存真实响应,parser 测试读 fixture 断言 4-tuple。fixture 文件:llama.cpp(chat_nostream/chat_stream/completions_nostream/completions_stream/messages_nostream/messages_stream/responses_nostream/responses_stream 已存)、embeddings_nostream、rerank_nostream;lmdeploy 实现 plan 时补(lmdeploy_chat_nostream/lmdeploy_chat_stream_iu/lmdeploy_responses_nostream/lmdeploy_responses_stream/lmdeploy_embedding/lmdeploy_rerank)。
- 每个解析器:流式 + 非流式各测;断言 4-tuple(llama.cpp chat 暖缓存:cache_n=15, prompt_n=4, output=8;lmdeploy chat:cache_n=0, prompt_n=17, output=13;embedding:prompt_n=prompt_tokens, output=0;rerank 同)。
- 异常安全:每个 parser 喂损坏/截断 fixture → `(0,0,0,0)` 不抛。
- dispatcher:`parse_tokens(path, body)` 路由正确;未知 path → `(0,0,0,0)`。
- include_usage 注入:单测 route_request 对流式 chat/completions 注入了 `stream_options.include_usage`,非流式/其他路径不注入。

**live 集成(Phase B 末尾,手动)**:经 manager(8080)分别打 chat/completions、messages、responses、embeddings、rerank,确认 DB 新记录非零且四元组正确;lmdeploy 后端流式请求确认注入 include_usage 后落库。

## 8. 不动的部分 / 非目标

- 计费公式、DB schema、webui、probe_registry、track-all、分析维度。
- **`probe_reranker` 向裸路径 `"rerank"` 发探测** —— lmdeploy 只认 `/v1/rerank`(404),会导致 lmdeploy reranker 模型冷启动健康检查失败。这是 Phase A 探测的遗留问题,**不在 Phase B 解析器范围**,单独跟踪修复。

## 9. 回滚

改动:新 `core/token_parsers.py` + `TokenTracker` 三方法加 path + `route_request` 两处传 path + 一处 include_usage 注入。纯增量 + 小改,无 DB 迁移。回滚 = git revert。
