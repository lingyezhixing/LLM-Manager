"""云端代理纯逻辑:族分类 / URL 拼接 / 鉴权头 / 映射解析。无 IO、无副作用,单测友好。

proxy.py 只做编排,URL/鉴权/映射决策全部收敛于此。
"""

from __future__ import annotations

from llm_manager.config import (
    AppConfig,
    CloudMapping,
    CloudModel,
    CloudProvider,
    parse_cloud_id,
)

_OPENAI_PREFIXES = (
    "v1/chat/completions",
    "v1/completions",
    "v1/embeddings",
    "v1/rerank",
    "rerank",
    "infill",
)
_RESPONSES_PREFIX = "v1/responses"
_CLAUDE_PREFIX = "v1/messages"


def _seg_startswith(path: str, prefix: str) -> bool:
    """前缀匹配以 '/' 为界:v1/messages 命中、v1/messages-batch 不命中。"""
    return path == prefix or path.startswith(prefix + "/")


def classify_path(path: str) -> str | None:
    """'openai' | 'responses' | 'claude' | None。"""
    p = path.lstrip("/").split("?")[0]
    for pre in _OPENAI_PREFIXES:
        if _seg_startswith(p, pre):
            return "openai"
    if _seg_startswith(p, _RESPONSES_PREFIX):
        return "responses"
    if _seg_startswith(p, _CLAUDE_PREFIX):
        return "claude"
    return None


def join_url(base: str, path: str, family: str) -> str:
    """OpenAI 传统+Responses 族剥 v1/;Claude 族路径原样。base 为厂商文档原样粘贴即用。"""
    p = path.lstrip("/").split("?")[0]
    if family in ("openai", "responses") and p.startswith("v1/"):
        p = p[3:]
    return base.rstrip("/") + "/" + p


def resolve_cloud_model(cfg: AppConfig, alias: str) -> tuple[str, CloudProvider, CloudModel] | None:
    """alias 解析到云目录模型:(provider_name, provider, cloud_model);未命中 → None。"""
    parsed = parse_cloud_id(alias)
    if parsed is None:
        return None
    provider_name, model_name = parsed
    p = cfg.cloud_providers.get(provider_name)
    if p is None:
        return None
    for m in p.models:
        if m.model_name == model_name:
            return provider_name, p, m
    return None


def mapping_for(provider: CloudProvider, path: str) -> CloudMapping | None:
    """服务商内映射:路径去前导 /、去 query 后精确等于 local_path。"""
    key = path.lstrip("/").split("?")[0]
    for m in provider.mappings:
        if m.local_path == key:
            return m
    return None


def resolve_global_mapping(cfg: AppConfig, path: str) -> tuple[CloudProvider, CloudMapping] | None:
    """model 缺失回退:全局映射表按路径精确命中(local_path 全局唯一,见 validate)。"""
    key = path.lstrip("/").split("?")[0]
    for p in cfg.cloud_providers.values():
        for m in p.mappings:
            if m.local_path == key:
                return p, m
    return None


def family_default_auth_style(family: str | None) -> str:
    """族规则路径的默认鉴权风格:OpenAI 传统/Responses=Bearer(OpenAI 规范),
    Claude=x-api-key(Anthropic 协议规范,Authorization Bearer 仅限 OAuth)。
    与各厂商 base「原样粘贴即用」承诺配套;偏离标准的上游经 extra_headers 覆盖。"""
    return "x-api-key" if family == "claude" else "bearer"


def build_auth_headers(
    provider: CloudProvider, family: str | None, auth_style: str
) -> dict[str, str]:
    """族默认鉴权头注入:api_key 空或 auth_style=none → 不注入;Claude 族补 anthropic-version。
    键名一律小写,与 apply_extra_headers 的键归一约定一致(大小写变体不会并存)。"""
    if auth_style == "none" or not provider.api_key:
        return {}
    if auth_style == "x-api-key":
        headers = {"x-api-key": provider.api_key}
    else:  # bearer(默认)
        headers = {"authorization": f"Bearer {provider.api_key}"}
    if family == "claude":
        headers.setdefault("anthropic-version", "2023-06-01")
    return headers


def apply_extra_headers(headers: dict[str, str], provider: CloudProvider) -> None:
    """provider 级 extra_headers 统一施加:'{key}' → api_key;替换后空 / 原值为空 → 不发;
    同名覆盖族默认;键名小写归一——用户填 'Authorization' 也写进 'authorization' 同键,
    避免与族默认并存成双鉴权头(部分上游 401)。"""
    for k, v in provider.extra_headers:
        if not k or not v:
            continue
        val = v.replace("{key}", provider.api_key)
        if val == "":
            continue
        headers[k.lower()] = val
