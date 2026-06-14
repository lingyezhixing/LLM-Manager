# Phase A: probe_registry + track-all Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `InterfacePlugin` class hierarchy with a flat `probe_registry`, remove the request-validation gate and the `TokenTracker` whitelist, and keep `mode` as a probe-selection + analytics-dimension field — all without touching token parsing (deferred to Phase B).

**Architecture:** New `core/probes.py` holds four plain probe functions (moved verbatim from the interface plugins) plus a `mode → fn` registry. `PluginManager.get_interface_plugin` becomes `get_probe(mode)`. `APIRouter.route_request` drops its validate gate (forward all paths). The `TokenTracker` whitelist is replaced by `get_all_model_modes()` at 9 call sites, then the two whitelist methods are deleted. `plugins/interfaces/` is removed entirely. `webui` and DB are untouched.

**Tech Stack:** Python 3, FastAPI, openai SDK, httpx, pandas/numpy, pytest 8.3.5 (introduced by this plan), Git Bash on Windows.

**Reference spec:** `docs/superpowers/specs/2026-06-14-probe-registry-track-all-design.md`

**Branch:** `refactor/probe-registry-phase-a` (already created; the spec commit is on it)

**Critical ordering constraint (spec §4.3):** migrate the 9 whitelist call sites BEFORE deleting the two config_manager methods; change `PluginManager.__init__` signature and its caller in the SAME commit; delete `plugins/interfaces/` only after nothing imports it.

---

## Task 1: Introduce pytest harness

**Files:**
- Create: `pytest.ini`
- Create: `requirements-dev.txt`
- Create: `tests/conftest.py`
- Create: `tests/test_sanity.py`

The project has no test suite today. This task adds a minimal one. Every later task adds tests here.

- [ ] **Step 1: Create pytest config**

Create `pytest.ini`:
```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_functions = test_*
```

- [ ] **Step 2: Create dev requirements**

Create `requirements-dev.txt`:
```text
pytest>=8.0.0
```

- [ ] **Step 3: Create conftest that pins cwd + sys.path**

`ConfigManager()` opens `config.yaml` and `Monitor` opens `webui/monitoring.db` as relative paths, so tests must run from the repo root. Create `tests/conftest.py`:
```python
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Put repo root on the path so `import core.probes` etc. work from anywhere.
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
# Pin cwd so relative config/db paths resolve.
os.chdir(REPO_ROOT)
```

- [ ] **Step 4: Write a sanity test**

Create `tests/test_sanity.py`:
```python
def test_pytest_runs():
    assert 1 + 1 == 2
```

- [ ] **Step 5: Run the harness to verify it works**

Run: `python -m pytest tests/test_sanity.py -v`
Expected: `1 passed`

- [ ] **Step 6: Commit**
```bash
git add pytest.ini requirements-dev.txt tests/conftest.py tests/test_sanity.py
git commit -m "test: 引入 pytest 测试框架"
```

---

## Task 2: Create `core/probes.py` (new component)

**Files:**
- Create: `core/probes.py`
- Create: `tests/test_probes.py`

Move the four `health_check` bodies verbatim out of the interface plugins into standalone functions with a pinned signature, plus a `probe_registry`. Nothing is deleted yet — the old plugins still exist and still work; this task only adds the new module.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_probes.py`:
```python
import inspect
import core.probes as probes


def test_probe_registry_has_four_modes():
    assert set(probes.probe_registry.keys()) == {"Chat", "Base", "Embedding", "Reranker"}


def test_probe_signatures_are_pinned():
    """Contract from spec §4.1: positional (model_alias, port, start_time, timeout)."""
    expected = ["model_alias", "port", "start_time", "timeout"]
    for mode, fn in probes.probe_registry.items():
        params = list(inspect.signature(fn).parameters.keys())
        assert params == expected, f"{mode} probe signature mismatch: {params}"


def test_probe_on_unreachable_port_returns_false():
    """A refused port must NOT return True. (~2s; guards against a signature/order
    swap that would make the probe no-op and flip status to ROUTING prematurely.)"""
    ok, msg = probes.probe_chat("nonexistent-model", port=1, start_time=None, timeout=1)
    assert ok is False
    assert isinstance(msg, str) and msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_probes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.probes'`

- [ ] **Step 3: Create `core/probes.py`**

Create `core/probes.py` (bodies moved verbatim from `plugins/interfaces/{chat,base,embedding,reranker}.py` `health_check`; only the class wrapper, `__init__`, and `InterfacePlugin` dependency are dropped):
```python
"""Health-check probes, selected by model `mode` at cold start.

Each probe has the pinned signature (model_alias, port, start_time, timeout) -> (bool, str).
Bodies are moved verbatim from the former plugins/interfaces/*.py health_check methods.
"""
import openai
import time
from typing import Tuple
import logging

logger = logging.getLogger(__name__)


def probe_chat(model_alias, port, start_time=None, timeout=300) -> Tuple[bool, str]:
    """聊天模型健康检查 - 先浅层检查，再深层检查"""
    if start_time is None:
        start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.models.list(timeout=3.0)
            break
        except Exception:
            time.sleep(2)
    else:
        return False, f"聊天探测器浅层检查超时: 服务在 {timeout} 秒内不可用"

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.chat.completions.create(
                model=model_alias,
                messages=[{"role": "user", "content": "hello"}],
                max_tokens=1,
                stream=False,
                timeout=5.0,
            )
            return True, "聊天探测器健康检查成功"
        except openai.APIConnectionError:
            pass
        except openai.APIStatusError:
            pass
        except openai.APITimeoutError:
            pass
        except Exception as e:
            logger.warning(f"聊天探测器深层检查意外错误: {e}")
        time.sleep(1)

    return False, "聊天探测器深层检查超时"


def probe_base(model_alias, port, start_time=None, timeout=300) -> Tuple[bool, str]:
    """基础文本补全模型健康检查 - 先浅层检查，再深层检查"""
    if start_time is None:
        start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.models.list(timeout=3.0)
            break
        except Exception:
            time.sleep(2)
    else:
        return False, f"基础探测器浅层检查超时: 服务在 {timeout} 秒内不可用"

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.completions.create(
                model=model_alias,
                prompt="hello",
                max_tokens=1,
                stream=False,
                timeout=5.0,
            )
            return True, "基础探测器健康检查成功"
        except openai.APIConnectionError:
            pass
        except openai.APIStatusError:
            pass
        except openai.APITimeoutError:
            pass
        except Exception as e:
            logger.warning(f"基础探测器深层检查意外错误: {e}")
        time.sleep(1)

    return False, "基础探测器深层检查超时"


def probe_embedding(model_alias, port, start_time=None, timeout=300) -> Tuple[bool, str]:
    """嵌入向量模型健康检查 - 先浅层检查，再深层检查"""
    if start_time is None:
        start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.models.list(timeout=3.0)
            break
        except Exception:
            time.sleep(2)
    else:
        return False, f"嵌入探测器浅层检查超时: 服务在 {timeout} 秒内不可用"

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.embeddings.create(
                model=model_alias,
                input="hello",
                encoding_format="float",
                timeout=5.0,
            )
            return True, "嵌入探测器健康检查成功"
        except openai.APIConnectionError:
            pass
        except openai.APIStatusError:
            pass
        except openai.APITimeoutError:
            pass
        except Exception as e:
            logger.warning(f"嵌入探测器深层检查意外错误: {e}")
        time.sleep(1)

    return False, "嵌入探测器深层检查超时"


def probe_reranker(model_alias, port, start_time=None, timeout=300) -> Tuple[bool, str]:
    """重排序模型健康检查 - 先浅层检查，再深层检查"""
    if start_time is None:
        start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.models.list(timeout=3.0)
            break
        except Exception:
            time.sleep(2)
    else:
        return False, f"重排序探测器浅层检查超时: 服务在 {timeout} 秒内不可用"

    while time.time() - start_time < timeout:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            response = client._client.post(
                "rerank",
                json={
                    "model": model_alias,
                    "query": "hello",
                    "documents": ["hello world", "test document"],
                    "top_n": 1,
                },
                timeout=5.0,
            )
            response.raise_for_status()
            return True, "重排序探测器健康检查成功"
        except openai.APIConnectionError:
            pass
        except openai.APIStatusError:
            pass
        except openai.APITimeoutError:
            pass
        except Exception as e:
            logger.warning(f"重排序探测器深层检查意外错误: {e}")
        time.sleep(1)

    return False, "重排序探测器深层检查超时"


probe_registry = {
    "Chat": probe_chat,
    "Base": probe_base,
    "Embedding": probe_embedding,
    "Reranker": probe_reranker,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_probes.py -v`
Expected: `3 passed` (the unreachable-port test takes ~2s)

- [ ] **Step 5: Commit**
```bash
git add core/probes.py tests/test_probes.py
git commit -m "feat: 新增 core/probes.py，从接口插件抽离健康探测器与 probe_registry"
```

---

## Task 3: Add `PluginManager.get_probe` and rewire the cold-start health check

**Files:**
- Modify: `core/plugin_system.py` (add `get_probe` method to `PluginManager`, ~after `get_interface_plugin` at line 389)
- Modify: `core/model_controller.py:800-813` (`_perform_health_checks`)
- Create: `tests/test_get_probe.py`

Add the new lookup alongside the old one (which still exists and still has callers — we delete it in Task 7). Switch the health-check call site to use it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_get_probe.py`:
```python
from core.plugin_system import PluginManager
from core.probes import probe_registry


def make_pm():
    # Constructor still takes (device_dir, interface_dir) until Task 7.
    return PluginManager("plugins/devices", "plugins/interfaces")


def test_get_probe_returns_registered_fn_for_each_mode():
    pm = make_pm()
    for mode, fn in probe_registry.items():
        assert pm.get_probe(mode) is fn


def test_get_probe_unknown_mode_returns_none():
    pm = make_pm()
    assert pm.get_probe("Bogus") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_get_probe.py -v`
Expected: FAIL with `AttributeError: 'PluginManager' object has no attribute 'get_probe'`

- [ ] **Step 3: Add `get_probe` to `PluginManager`**

In `core/plugin_system.py`, inside `class PluginManager`, add this method (place it right after the existing `get_interface_plugin` method, around line 391):
```python
    def get_probe(self, mode: str):
        """按 mode 取健康探测函数 (probe_registry 查找)。

        替代旧的 get_interface_plugin 用于冷启动健康检查。
        返回 None 表示该 mode 未注册探测器。
        """
        from core.probes import probe_registry
        return probe_registry.get(mode)
```

- [ ] **Step 4: Rewire the health-check call site in `model_controller.py`**

In `core/model_controller.py`, `_perform_health_checks` (lines 800-813), replace:
```python
        mode = model_config.get("mode", "Chat")
        plugin = self.plugin_manager.get_interface_plugin(mode)

        if not plugin:
            msg = f"未找到模式 '{mode}' 的接口插件"
            self._handle_startup_failure(primary_name, msg)
            return False, msg

        # [Checkpoint 5] 检查前
        if self._check_if_cancelled(primary_name):
            return False, "启动中断（阶段5）"

        # 调用插件进行检查
        ok, msg = plugin.health_check(primary_name, port, start_ts, timeout)
```
with:
```python
        mode = model_config.get("mode", "Chat")
        probe_fn = self.plugin_manager.get_probe(mode)

        if not probe_fn:
            msg = f"未找到模式 '{mode}' 的健康探测器"
            self._handle_startup_failure(primary_name, msg)
            return False, msg

        # [Checkpoint 5] 检查前
        if self._check_if_cancelled(primary_name):
            return False, "启动中断（阶段5）"

        # 调用探测器进行检查 (签名: model_alias, port, start_time, timeout)
        ok, msg = probe_fn(primary_name, port, start_ts, timeout)
```
The lines after (815-829: checkpoint 6, ROUTING flip, runtime record, failure handling) stay unchanged.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_get_probe.py tests/test_probes.py -v`
Expected: `5 passed`

- [ ] **Step 6: Verify the app still imports cleanly**

Run: `python -c "from core.model_controller import ModelController; print('ok')"`
Expected: prints `ok` (no import error).

- [ ] **Step 7: Commit**
```bash
git add core/plugin_system.py core/model_controller.py tests/test_get_probe.py
git commit -m "refactor: 冷启动健康检查改用 probe_registry.get_probe 取代接口插件"
```

---

## Task 4: Remove the validate gate in `route_request`

**Files:**
- Modify: `core/api_router.py:272-279`
- Create: `tests/test_route_no_gate.py`

After Task 3, `get_interface_plugin` has only one remaining caller: this gate. Deleting the gate removes that caller, leaving `get_interface_plugin` unused (it is deleted in Task 7).

- [ ] **Step 1: Write the failing test**

Create `tests/test_route_no_gate.py`:
```python
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_route_request_no_longer_calls_validate_or_interface_plugin():
    """After Task 4, route_request must not reference validate_request or
    get_interface_plugin. Guards against leaving the gate in place."""
    src = open(os.path.join(REPO_ROOT, "core", "api_router.py"), encoding="utf-8").read()
    # Locate route_request body heuristically.
    start = src.index("async def route_request")
    body = src[start:]
    assert "validate_request" not in body, "route_request still calls validate_request"
    assert "get_interface_plugin" not in body, "route_request still uses get_interface_plugin"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_route_no_gate.py -v`
Expected: FAIL (the gate still references `get_interface_plugin` and `validate_request`)

- [ ] **Step 3: Delete the gate**

In `core/api_router.py`, inside `route_request`, delete these lines (currently 272-279):
```python
        model_mode = model_config.get("mode", "Chat")
        interface_plugin = self.model_controller.plugin_manager.get_interface_plugin(model_mode)
        if not interface_plugin:
            raise HTTPException(status_code=400, detail=f"不支持的模型模式: {model_mode}")

        is_valid, error_message = interface_plugin.validate_request(path, model_name)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_message)
```
Keep the `get_model_config` call above (lines 268-270) — `model_config['port']` is still needed downstream. The code now flows directly from the `model_config` fetch into `self.increment_pending_requests(model_name)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_route_no_gate.py -v`
Expected: `1 passed`

- [ ] **Step 5: Verify app imports**

Run: `python -c "from core.api_router import APIRouter; print('ok')"`
Expected: prints `ok`

- [ ] **Step 6: Commit**
```bash
git add core/api_router.py tests/test_route_no_gate.py
git commit -m "refactor: 移除 route_request 的 validate 闸门，全部路径转发"
```

---

## Task 5: track-all — add `get_all_model_modes` and migrate the 9 whitelist call sites

**Files:**
- Modify: `core/config_manager.py` (add `get_all_model_modes`)
- Modify: `core/api_router.py:21` and `:133-136`
- Modify: `core/api_server.py:168-187, 224, 510, 654, 708, 768`
- Create: `tests/test_get_all_model_modes.py`

This task migrates all 9 call sites to the new helper. The two old methods (`get_token_tracker_modes`, `should_track_tokens_for_mode`) are NOT deleted yet — they still exist, just unused. Deleting them is Task 6.

- [ ] **Step 1: Write the failing test**

Create `tests/test_get_all_model_modes.py`:
```python
from core.config_manager import ConfigManager


def test_get_all_model_modes_returns_sorted_distinct_strings():
    cm = ConfigManager()
    modes = cm.get_all_model_modes()
    assert isinstance(modes, list)
    assert modes == sorted(set(modes)), "must be de-duplicated and sorted"
    assert all(isinstance(m, str) for m in modes)
    # The real config.yaml ships at least Chat; sanity-check it's present.
    assert "Chat" in modes


def test_get_all_model_modes_is_subset_of_known_modes():
    cm = ConfigManager()
    modes = set(cm.get_all_model_modes())
    assert modes.issubset({"Chat", "Base", "Embedding", "Reranker"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_get_all_model_modes.py -v`
Expected: FAIL with `AttributeError: 'ConfigManager' object has no attribute 'get_all_model_modes'`

- [ ] **Step 3: Add `get_all_model_modes` to `ConfigManager`**

In `core/config_manager.py`, add this method (place it right after `get_token_tracker_modes`, around line 206):
```python
    def get_all_model_modes(self) -> List[str]:
        """返回所有模型配置中出现的 distinct mode（去重、排序）。

        track-all 之后取代 get_token_tracker_modes()，作为分析看板
        mode_breakdown 维度的键来源。
        """
        return sorted({cfg.get("mode", "Chat") for cfg in self.get_all_model_configs().values()})
```

- [ ] **Step 4: Migrate `api_router.py:21` (TokenTracker init log)**

In `core/api_router.py`, `TokenTracker.__init__`, replace:
```python
        logger.info(f"[TokenTracker] 初始化完成, 当前追踪模式: {self.config_manager.get_token_tracker_modes()}")
```
with:
```python
        logger.info("[TokenTracker] 初始化完成, 追踪全部转发的 LLM 流量 (track-all)")
```

- [ ] **Step 5: Migrate `api_router.py:133-136` (record guard)**

In `core/api_router.py`, `record_request_tokens`, delete the whitelist guard block:
```python
            model_mode = self.config_manager.get_model_mode(model_name)
            if not self.config_manager.should_track_tokens_for_mode(model_mode):
                logger.debug(f"[TokenTracker] 忽略记录: 模型 {model_name} (模式 {model_mode}) 不在追踪列表中")
                return
```
The all-zero guard immediately below (`if not any([...]): ... return`) stays. If the success-log on the following line (currently `logger.debug(f"[TokenTracker] 记录成功: 模型 {model_name} (模式 {model_mode}), ...")`) references `model_mode`, replace `model_mode` in that f-string with `self.config_manager.get_model_mode(model_name)` inline, or simply drop the `(模式 ...)` clause. Minimal edit:
```python
            logger.debug(f"[TokenTracker] 记录成功: 模型 {model_name}, 总Tokens {input_tokens + output_tokens}, cache_n: {cache_n}, prompt_n: {prompt_n}")
```

- [ ] **Step 6: Migrate `api_server.py:168-170` (inner helper filter)**

In `core/api_server.py`, inside `_get_enriched_requests_dataframe`'s `get_model_requests_with_mode`, delete the early-return filter:
```python
                mode = self.config_manager.get_model_mode(model_name)
                if not self.config_manager.should_track_tokens_for_mode(mode):
                    return []
```
Keep the rest of the function. The `mode` value is still needed on the return line (`model_mode=mode`), so re-fetch it inline on that line:
```python
                return [dict(req.__dict__, model_name=model_name, model_mode=self.config_manager.get_model_mode(model_name)) for req in requests]
```

- [ ] **Step 7: Migrate `api_server.py:184-187` (model list filter)**

In `_get_enriched_requests_dataframe`, replace:
```python
        model_names_to_track = [
            name for name in self.config_manager.get_model_names()
            if self.config_manager.should_track_tokens_for_mode(self.config_manager.get_model_mode(name))
        ]
```
with:
```python
        model_names_to_track = self.config_manager.get_model_names()
```

- [ ] **Step 8: Migrate the 5 breakdown-key sites**

In `core/api_server.py`, replace each `tracked_modes = self.config_manager.get_token_tracker_modes()` with `tracked_modes = self.config_manager.get_all_model_modes()` at these five locations:
- `_calculate_hourly_cost_trends` (~line 224)
- `get_throughput` (~line 510)
- `get_usage_summary` (~line 654)
- `get_token_trends` (~line 708)
- `get_cost_trends` (~line 768)

(Use an editor search-and-replace on the exact string `tracked_modes = self.config_manager.get_token_tracker_modes()` → there are exactly 5 occurrences, all replaced identically.)

- [ ] **Step 9: Run the test to verify it passes**

Run: `python -m pytest tests/test_get_all_model_modes.py -v`
Expected: `2 passed`

- [ ] **Step 10: Verify no remaining functional use of the whitelist (grep)**

Run: `grep -rn "should_track_tokens_for_mode\|get_token_tracker_modes" core/`
Expected: output shows ONLY the two method definitions in `core/config_manager.py` (lines ~203 and ~207) — no other call sites remain. If any call site shows up, fix it before continuing.

- [ ] **Step 11: Verify app imports**

Run: `python -c "from core.api_server import APIServer; print('ok')"`
Expected: prints `ok`

- [ ] **Step 12: Commit**
```bash
git add core/config_manager.py core/api_router.py core/api_server.py tests/test_get_all_model_modes.py
git commit -m "refactor: track-all —— 9 处白名单调用点迁移到 get_all_model_modes"
```

---

## Task 6: Delete the two whitelist methods + the config key

**Files:**
- Modify: `core/config_manager.py:203-210` (delete `get_token_tracker_modes` and `should_track_tokens_for_mode`)
- Modify: `config.yaml` (remove the `TokenTracker` block)
- Create: `tests/test_whitelist_gone.py`

Now that all call sites are migrated (verified by Task 5 Step 10), the two methods are dead. Delete them and the config key.

- [ ] **Step 1: Write the failing test (currently fails: methods still exist)**

Create `tests/test_whitelist_gone.py`:
```python
import core.config_manager as cm_mod
from core.config_manager import ConfigManager


def test_whitelist_methods_removed():
    assert not hasattr(ConfigManager, "get_token_tracker_modes"), \
        "get_token_tracker_modes must be deleted"
    assert not hasattr(ConfigManager, "should_track_tokens_for_mode"), \
        "should_track_tokens_for_mode must be deleted"


def test_no_dangling_whitelist_references_in_source():
    import os
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hits = []
    for sub in ("core",):
        base = os.path.join(repo, sub)
        for root, _, files in os.walk(base):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                for i, line in enumerate(open(path, encoding="utf-8"), 1):
                    for needle in ("get_token_tracker_modes", "should_track_tokens_for_mode"):
                        if needle in line:
                            hits.append(f"{path}:{i}: {line.strip()}")
    assert not hits, "dangling whitelist refs:\n" + "\n".join(hits)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_whitelist_gone.py -v`
Expected: FAIL (the two methods still exist on ConfigManager)

- [ ] **Step 3: Delete the two methods**

In `core/config_manager.py`, delete these two methods entirely (currently lines 203-210):
```python
    def get_token_tracker_modes(self) -> List[str]:
        """获取需要追踪token的模型模式列表"""
        return self.get_program_config().get('TokenTracker', ["Chat", "Base", "Embedding", "Reranker"])

    def should_track_tokens_for_mode(self, mode: str) -> bool:
        """检查指定模式的模型是否需要追踪token"""
        tracked_modes = self.get_token_tracker_modes()
        return mode in tracked_modes
```

- [ ] **Step 4: Remove the TokenTracker block from config.yaml**

In `config.yaml`, under `program:`, delete the `TokenTracker:` list block (the 4-5 lines defining the tracked-modes list). Leave every other `program:` key intact (`host`, `port`, `log_level`, `alive_time`, `device_plugin_dir`, `interface_plugin_dir`, etc.).

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_whitelist_gone.py -v`
Expected: `2 passed`

- [ ] **Step 6: Run full suite + import smoke**

Run: `python -m pytest tests/ -v && python -c "from core.config_manager import ConfigManager; ConfigManager(); print('config ok')"`
Expected: all tests pass; prints `config ok`

- [ ] **Step 7: Commit**
```bash
git add core/config_manager.py config.yaml tests/test_whitelist_gone.py
git commit -m "refactor: 删除 TokenTracker 白名单方法与配置项，完成 track-all"
```

---

## Task 7: Clean up the interface-plugin plumbing in `plugin_system.py` + signature change

**Files:**
- Modify: `core/plugin_system.py` (delete `InterfacePluginLoader`; remove interface fields/methods from `PluginManager`; change `__init__` signature)
- Modify: `core/model_controller.py:385-393` (`load_plugins` call site — MUST change in the same commit as the signature change)
- Modify: `core/config_manager.py:180-182` (delete orphaned `get_interface_plugin_dir`)
- Create: `tests/test_plugin_system_no_interfaces.py`

After Tasks 3 & 4, nothing calls `get_interface_plugin` / `get_all_interface_plugins` / `get_interface_loader`, and `interface_dir`/`interface_loader`/`interface_plugins` are only referenced internally by `PluginManager`. This task removes all of it.

> **Same-commit requirement:** `PluginManager.__init__` drops its `interface_dir` param, and `model_controller.load_plugins` is its only caller — edit both in Step 4/5 before committing, or the app crashes with `TypeError` at startup.

- [ ] **Step 1: Write the failing test**

Create `tests/test_plugin_system_no_interfaces.py`:
```python
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANNED = [
    "InterfacePluginLoader",
    "interface_loader",
    "interface_plugins",
    "get_interface_plugin",   # catches get_interface_plugin / get_all_interface_plugins
    "get_interface_plugin_dir",
    "interface_dir",
    "from plugins.interfaces",
    "plugins.interfaces.Base_Class",
]


def _scan(*subdirs):
    hits = []
    for sub in subdirs:
        base = os.path.join(REPO_ROOT, sub)
        for root, _, files in os.walk(base):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                for i, line in enumerate(open(path, encoding="utf-8"), 1):
                    for needle in BANNED:
                        if needle in line:
                            hits.append(f"{path}:{i}: {line.strip()}")
    return hits


def test_no_interface_refs_in_core():
    assert not _scan("core"), "dangling interface refs in core/:\n" + "\n".join(_scan("core"))


def test_pluginmanager_init_has_no_interface_dir():
    import inspect
    from core.plugin_system import PluginManager
    params = list(inspect.signature(PluginManager.__init__).parameters.keys())
    assert "interface_dir" not in params, f"PluginManager.__init__ still takes interface_dir: {params}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_plugin_system_no_interfaces.py -v`
Expected: FAIL (interface refs still present)

- [ ] **Step 3: Edit `core/plugin_system.py`**

Apply these edits:

(a) Delete the entire `InterfacePluginLoader` class (currently lines 170-189).

(b) In `PluginManager.__init__` (currently 194-200), change the signature and drop interface fields. Replace:
```python
    def __init__(self, device_dir: str = "plugins/devices", interface_dir: str = "plugins/interfaces"):
        self.device_dir = device_dir
        self.interface_dir = interface_dir
        self.device_loader = DevicePluginLoader(device_dir)
        self.interface_loader = InterfacePluginLoader(interface_dir)
        self.device_plugins: Dict[str, Any] = {}
        self.interface_plugins: Dict[str, Any] = {}
        self.last_reload_time = 0
```
with:
```python
    def __init__(self, device_dir: str = "plugins/devices"):
        self.device_dir = device_dir
        self.device_loader = DevicePluginLoader(device_dir)
        self.device_plugins: Dict[str, Any] = {}
        self.last_reload_time = 0
```

(c) In `load_all_plugins`, delete the interface branch: remove the `interface_plugins` key from the result dict, and remove the try/except block that sets `self.interface_loader.model_manager` and calls `self.interface_loader.load_plugins(...)`. Keep the device-loading branch.

(d) In `reload_plugins`, delete `self.interface_plugins.clear()`.

(e) Delete the methods `get_interface_plugin`, `get_all_interface_plugins`, `get_interface_loader`. Keep `get_probe` (added in Task 3).

(f) In `get_plugin_status` and `discover_new_plugins` (both currently dead code with zero callers), delete the `interface_plugins`/`interface_dir` blocks so they no longer reference removed attributes.

- [ ] **Step 4: Edit `core/model_controller.py:385-393` (the PluginManager caller)**

Replace:
```python
    def load_plugins(self):
        """加载插件并刷新初始设备状态"""
        device_dir = self.config_manager.get_device_plugin_dir()
        interface_dir = self.config_manager.get_interface_plugin_dir()
        self.plugin_manager = PluginManager(device_dir, interface_dir)

        try:
            self.plugin_manager.load_all_plugins(model_manager=self)
            logger.info(f"插件加载完毕: 设备插件 {len(self.plugin_manager.get_all_device_plugins())} 个, 接口插件 {len(self.plugin_manager.get_all_interface_plugins())} 个")
```
with:
```python
    def load_plugins(self):
        """加载插件并刷新初始设备状态"""
        device_dir = self.config_manager.get_device_plugin_dir()
        self.plugin_manager = PluginManager(device_dir)

        try:
            self.plugin_manager.load_all_plugins(model_manager=self)
            logger.info(f"插件加载完毕: 设备插件 {len(self.plugin_manager.get_all_device_plugins())} 个")
```

- [ ] **Step 5: Delete `get_interface_plugin_dir` from `core/config_manager.py`**

Delete the method (currently lines 180-182):
```python
    def get_interface_plugin_dir(self) -> str:
        """获取接口插件目录"""
        return self.get_program_config().get('interface_plugin_dir', 'plugins/interfaces')
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_plugin_system_no_interfaces.py -v`
Expected: `2 passed`

- [ ] **Step 7: Verify full suite + app import**

Run: `python -m pytest tests/ -v && python -c "from core.model_controller import ModelController; print('ok')"`
Expected: all tests pass; prints `ok`

- [ ] **Step 8: Commit**
```bash
git add core/plugin_system.py core/model_controller.py core/config_manager.py tests/test_plugin_system_no_interfaces.py
git commit -m "refactor: 删除接口插件加载体系，PluginManager 不再管 interface"
```

---

## Task 8: Delete the `plugins/interfaces/` directory

**Files:**
- Delete: `plugins/interfaces/Base_Class.py`, `base.py`, `chat.py`, `embedding.py`, `reranker.py`, `__init__.py`

After Task 7, nothing imports `plugins.interfaces`. Remove the directory.

- [ ] **Step 1: Confirm nothing imports it**

Run: `grep -rn "plugins.interfaces\|plugins\.interfaces\|from plugins.interfaces" core/ plugins/ main.py`
Expected: no output (no matches). If anything matches, stop and fix the importer first.

- [ ] **Step 2: Delete the directory**
```bash
git rm -r plugins/interfaces
```

- [ ] **Step 3: Verify app imports + full suite**

Run: `python -m pytest tests/ -v && python -c "import core.api_server, core.model_controller, core.probes; print('imports ok')"`
Expected: all tests pass; prints `imports ok`

- [ ] **Step 4: Commit**
```bash
git commit -m "refactor: 删除 plugins/interfaces 目录（已被 core/probes.py 取代）"
```

---

## Task 9: Add `mode` validation to `validate_config`

**Files:**
- Modify: `core/config_manager.py` (`validate_config`, ~line 239)
- Create: `tests/test_validate_config_modes.py`

With the gate gone, a misspelled `mode` no longer fails at request time — it would fail confusingly at cold start ("未找到模式 'Caht' 的健康探测器"). Catch it at config-load time instead (spec §3.7).

- [ ] **Step 1: Write the failing test**

Create `tests/test_validate_config_modes.py`:
```python
from core.config_manager import ConfigManager

SUPPORTED = {"Chat", "Base", "Embedding", "Reranker"}


def test_real_config_has_no_mode_errors():
    cm = ConfigManager()
    errors = cm.validate_config()
    # No error string should mention an unsupported mode for the shipped config.
    mode_errors = [e for e in errors if "模式" in e or "mode" in e.lower()]
    assert mode_errors == [], f"unexpected mode errors: {mode_errors}"


def test_bad_mode_is_reported(tmp_path, monkeypatch):
    import yaml
    cm = ConfigManager()
    # Inject one bogus mode into the in-memory config.
    first_key = next(iter(cm.config["Local-Models"]))
    original = cm.config["Local-Models"][first_key].get("mode")
    cm.config["Local-Models"][first_key]["mode"] = "Caht"
    try:
        errors = cm.validate_config()
        assert any("Caht" in e for e in errors), f"expected an error naming the bad mode, got: {errors}"
    finally:
        if original is not None:
            cm.config["Local-Models"][first_key]["mode"] = original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_validate_config_modes.py -v`
Expected: the second test FAILS (no mode validation yet → no error naming "Caht").

- [ ] **Step 3: Add the validation**

In `core/config_manager.py`, `validate_config`, inside the `for key, model_cfg in local_models.items():` loop, add a mode check. After the existing `required_model_keys` check block, add:
```python
                # 校验 mode 是受支持的健康探测器之一
                mode_val = model_cfg.get('mode', 'Chat')
                if mode_val not in ('Chat', 'Base', 'Embedding', 'Reranker'):
                    errors.append(f"模型 '{key}' 的 mode '{mode_val}' 不受支持 (支持: Chat, Base, Embedding, Reranker)")
```
(Place it within the loop body, before the device-config checks, so a bad mode is reported per-model.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_validate_config_modes.py -v`
Expected: `2 passed`

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/ -v`
Expected: all tests pass

- [ ] **Step 6: Commit**
```bash
git add core/config_manager.py tests/test_validate_config_modes.py
git commit -m "feat: validate_config 校验模型 mode 必须是受支持的探测器"
```

---

## Task 10: Manual end-to-end verification (live server)

**Files:** none (verification only)

The automated tests cover contracts and regressions. This task verifies the live behavior against the running app + the llama.cpp server at `127.0.0.1:10006`. Run these from a second terminal while the app runs.

- [ ] **Step 1: Start the app**

Run: `python main.py`
Wait for the tray/API to come up. Confirm it listens on the configured port (default 8080 per `config.yaml`).

- [ ] **Step 2: Health + no-crash smoke**

Run:
```bash
curl -s http://127.0.0.1:8080/api/health
curl -s "http://127.0.0.1:8080/api/analytics/usage-summary/0/9999999999"
curl -s "http://127.0.0.1:8080/api/metrics/throughput/current-session"
```
Expected: all return JSON with `"success": true` and NO 500 / AttributeError. (This directly exercises the migrated analytics call sites.)

- [ ] **Step 3: Cold-start a Chat model via the probe_registry path**

Using a model alias from your `config.yaml` (e.g. `Qwen3.6-27B`):
```bash
curl -s -X POST http://127.0.0.1:8080/api/models/Qwen3.6-27B/start
```
Expected: `{"success": true, ...}`. The app log should show the new probe wording ("聊天探测器…"), NOT "接口插件". Status should reach `routing`.

- [ ] **Step 4: Forward-all + track-all: a chat request lands in the DB**

Send a non-streaming chat completion through the manager:
```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3.6-27B","messages":[{"role":"user","content":"ping"}],"max_tokens":5,"stream":false}'
```
Expected: a normal OpenAI-format response. Then verify a row was recorded:
```bash
python -c "import sqlite3,time; c=sqlite3.connect('webui/monitoring.db'); rows=c.execute('SELECT start_time,end_time,input_tokens,output_tokens,cache_n,prompt_n FROM model_requests ORDER BY id DESC LIMIT 1').fetchall(); print(rows)"
```
Expected: the last row has non-zero `input_tokens`/`output_tokens` (chat/completions timings path still works post-refactor) and a recent `end_time`.

- [ ] **Step 5: Confirm no-gate behavior**

Send a path the model still serves but observe forwarding works (no 400 from a missing gate). For example, `/v1/messages` (Anthropic format) against the same Chat model — it should forward to the upstream (Phase A does not parse its tokens, so the row's tokens may be 0 for this path; that is expected and is fixed in Phase B):
```bash
curl -s http://127.0.0.1:8080/v1/messages -H "Content-Type: application/json" -d '{"model":"Qwen3.6-27B","max_tokens":5,"messages":[{"role":"user","content":"ping"}]}'
```
Expected: an Anthropic-format response (not a local 400 about "不支持路径"). If a row is written with all-zero tokens, that is the known Phase A limitation, not a bug.

- [ ] **Step 6: Document the run**

If all steps pass, Phase A is verified. Note any deviations. (No commit unless docs change.)

---

## Self-Review (completed by plan author)

**Spec coverage:**
- §3.1 `core/probes.py` → Task 2 ✓
- §3.2 delete `plugins/interfaces/` → Task 8 ✓
- §3.3 plugin_system edits → Task 7 ✓
- §3.4 model_controller edits → Task 3 (health) + Task 7 (load_plugins) ✓
- §3.5 route_request gate removal → Task 4 ✓
- §3.6 track-all 9 sites + helper + method deletion → Tasks 5 & 6 ✓
- §3.7 config TokenTracker removal + validate_config mode check → Tasks 6 & 9 ✓
- §4 contracts (probe signature pinned → Task 2 test; ordering → task sequence) ✓
- §5 behavior changes → verified in Task 10 ✓
- §7 tests → Tasks 2,3,5,6,7,9 (automated) + Task 10 (manual) ✓

**Placeholder scan:** none — every code step contains the actual code or exact before/after.

**Type/name consistency:** `get_probe` (Task 3) used consistently; `probe_registry` (Task 2) consistent; `get_all_model_modes` (Tasks 5-6) consistent; the pinned probe signature `(model_alias, port, start_time, timeout)` consistent across Task 2 impl, Task 2 test, Task 3 call site, and the spec.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-14-probe-registry-track-all.md`.
