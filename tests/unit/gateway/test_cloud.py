"""gateway/cloud.py 纯逻辑:族分类 / URL 拼接 / 鉴权 / 映射。无 IO。"""

from __future__ import annotations

import pytest

from llm_manager.config import (
    AppConfig,
    CloudMapping,
    CloudModel,
    CloudProvider,
    ProgramConfig,
)
from llm_manager.gateway import cloud


def _provider(**kw) -> CloudProvider:
    return CloudProvider(**{"name": "ds", "api_key": "sk-1", **kw})


def _cfg(providers=None) -> AppConfig:
    return AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={},
        wol=None,
        claude_configs={},
        cloud_providers=providers or {},
    )


@pytest.mark.parametrize(
    ("base", "path", "family", "expected"),
    [
        (
            "https://api.deepseek.com",
            "v1/chat/completions",
            "openai",
            "https://api.deepseek.com/chat/completions",
        ),
        (
            "https://api.deepseek.com/anthropic",
            "v1/messages",
            "claude",
            "https://api.deepseek.com/anthropic/v1/messages",
        ),
        (
            "https://open.bigmodel.cn/api/paas/v4",
            "v1/embeddings",
            "openai",
            "https://open.bigmodel.cn/api/paas/v4/embeddings",
        ),
        (
            "https://open.bigmodel.cn/api/anthropic",
            "v1/messages",
            "claude",
            "https://open.bigmodel.cn/api/anthropic/v1/messages",
        ),
        (
            "https://api.siliconflow.cn/v1",
            "v1/rerank",
            "openai",
            "https://api.siliconflow.cn/v1/rerank",
        ),
        (
            "https://api.openai.com/v1",
            "v1/responses",
            "responses",
            "https://api.openai.com/v1/responses",
        ),
        (
            "https://api.anthropic.com",
            "v1/messages/count_tokens",
            "claude",
            "https://api.anthropic.com/v1/messages/count_tokens",
        ),
    ],
)
def test_join_url_real_world(base, path, family, expected):
    assert cloud.join_url(base, path, family) == expected


def test_join_url_base_trailing_slash_and_native_paths():
    assert (
        cloud.join_url("https://x/", "v1/chat/completions", "openai")
        == "https://x/chat/completions"
    )
    assert (
        cloud.join_url("https://api.siliconflow.cn/v1", "rerank", "openai")
        == "https://api.siliconflow.cn/v1/rerank"
    )
    assert (
        cloud.join_url("https://api.siliconflow.cn/v1", "infill", "openai")
        == "https://api.siliconflow.cn/v1/infill"
    )


def test_classify_path_segment_boundary():
    assert cloud.classify_path("v1/messages") == "claude"
    assert cloud.classify_path("v1/messages/count_tokens") == "claude"
    assert cloud.classify_path("v1/messages-batch") is None  # 前缀以 '/' 为界
    assert cloud.classify_path("v1/chat/completions") == "openai"
    assert cloud.classify_path("v1/responses") == "responses"
    assert cloud.classify_path("rerank") == "openai"
    assert cloud.classify_path("v1/tokenize") is None


def test_resolve_cloud_model():
    p = _provider(models=(CloudModel(model_name="deepseek-chat"),))
    cfg = _cfg({"ds": p})
    got = cloud.resolve_cloud_model(cfg, "ds/deepseek-chat")
    assert got is not None and got[0] == "ds" and got[2].model_name == "deepseek-chat"
    assert cloud.resolve_cloud_model(cfg, "ds/nope") is None
    assert cloud.resolve_cloud_model(cfg, "local-model") is None
    assert cloud.resolve_cloud_model(cfg, "a/b/c") is None


def test_mapping_and_global_mapping():
    p = _provider(mappings=(CloudMapping(local_path="v1/x", target_url="https://x/api"),))
    cfg = _cfg({"ds": p})
    assert cloud.mapping_for(p, "v1/x?foo=1").local_path == "v1/x"
    assert cloud.mapping_for(p, "v1/y") is None
    got = cloud.resolve_global_mapping(cfg, "v1/x")
    assert got is not None and got[0].name == "ds"


def test_build_auth_headers_families():
    p = _provider(api_key="K")
    assert cloud.build_auth_headers(p, "openai", "bearer") == {"authorization": "Bearer K"}
    assert cloud.build_auth_headers(p, "responses", "bearer") == {"authorization": "Bearer K"}
    claude = cloud.build_auth_headers(p, "claude", "bearer")
    assert claude == {"authorization": "Bearer K", "anthropic-version": "2023-06-01"}
    assert cloud.build_auth_headers(p, "claude", "x-api-key") == {
        "x-api-key": "K",
        "anthropic-version": "2023-06-01",
    }
    assert cloud.build_auth_headers(p, None, "none") == {}
    assert cloud.build_auth_headers(_provider(api_key=""), "openai", "bearer") == {}


def test_apply_extra_headers_override_and_placeholder():
    p = _provider(
        api_key="K",
        extra_headers=(("X-A", "{key}"), ("X-B", ""), ("authorization", "Bearer OTHER")),
    )
    h = {"authorization": "Bearer K"}
    cloud.apply_extra_headers(h, p)
    assert h["x-a"] == "K"  # 键名小写归一
    assert "X-A" not in h
    assert "X-B" not in h and "x-b" not in h  # 原值为空 → 不发
    assert h["authorization"] == "Bearer OTHER"  # 同名覆盖族默认


def test_apply_extra_headers_case_variant_key_overrides_family_default():
    """I1:extra_headers 用规范大写 'Authorization' 时,必须覆盖族默认小写
    'authorization',不得并存两个键(上游收到双鉴权头)。"""
    p = _provider(api_key="K", extra_headers=(("Authorization", "Bearer OTHER"),))
    h = {"authorization": "Bearer K"}
    cloud.apply_extra_headers(h, p)
    assert h == {"authorization": "Bearer OTHER"}  # 唯一键,值被覆盖
