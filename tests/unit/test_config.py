from llm_manager.config import (
    AppConfig,
    CloudMapping,
    CloudModel,
    CloudProvider,
    Command,
    ModelConfig,
    ModelMode,
    ProgramConfig,
    Scheme,
    parse_cloud_id,
    resolve_alias,
    select_adaptive,
    substitute_vars,
    validate,
)


def test_model_mode_values():
    assert {m.value for m in ModelMode} == {"Chat", "Embedding", "Reranker"}


def test_validate_flags_port_and_alias_clash_and_bad_mode():
    cfg = AppConfig(
        program=ProgramConfig(host="0.0.0.0", port=8080, alive_time=60, log_level="INFO"),
        models={
            "A": ModelConfig(aliases=("x",), mode="Chat", port=1, auto_start=False, schemes={}),
            "B": ModelConfig(
                aliases=("x",), mode="Embedding", port=1, auto_start=False, schemes={}
            ),
            "C": ModelConfig(aliases=("y",), mode="Bogus", port=2, auto_start=False, schemes={}),
            "D": ModelConfig(aliases=("z",), mode="Chat", port=3, auto_start=False, schemes={}),
        },
        wol=None,
        claude_configs={},
    )
    errs = validate(cfg)
    assert any("Port 1 shared" in e for e in errs)
    assert any("Alias 'x' shared" in e for e in errs)
    assert any("mode 'Bogus'" in e for e in errs)
    assert any("no device scheme" in e for e in errs)


def test_validate_passes_clean_config():
    cfg = AppConfig(
        program=ProgramConfig(host="0.0.0.0", port=8080, alive_time=60, log_level="INFO"),
        models={
            "A": ModelConfig(
                aliases=("a",),
                mode="Chat",
                port=1,
                auto_start=False,
                schemes={"S": Scheme("S", frozenset({"gpu"}), Command(exe="a.bat"), {"gpu": 1})},
            )
        },
        wol=None,
        claude_configs={},
    )
    assert validate(cfg) == []


def test_select_adaptive_first_subset_wins():
    s_gpu = Scheme("GPU", frozenset({"gpu"}), Command(exe="g.bat"), {"gpu": 1})
    s_apu = Scheme("APU", frozenset({"apu"}), Command(exe="a.bat"), {"apu": 1})
    m = ModelConfig(
        aliases=("M",),
        mode="Chat",
        port=1,
        auto_start=False,
        schemes={"GPU": s_gpu, "APU": s_apu},
    )
    assert select_adaptive(m, {"gpu"}).config_source == "GPU"
    assert select_adaptive(m, {"apu"}).config_source == "APU"
    assert select_adaptive(m, set()) is None


def test_resolve_alias_to_primary():
    cfg = AppConfig(
        program=ProgramConfig(host="0.0.0.0", port=8080, alive_time=60, log_level="INFO"),
        models={"Qwen3-4B": ModelConfig(aliases=("Qwen3-4B", "q4"), mode="Chat", port=1)},
        wol=None,
        claude_configs={},
    )
    assert resolve_alias(cfg, "q4") == "Qwen3-4B"
    assert resolve_alias(cfg, "Qwen3-4B") == "Qwen3-4B"
    try:
        resolve_alias(cfg, "nope")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_referenced_devices_unions_required_and_memory_keys():
    from llm_manager.config import (
        AppConfig,
        Command,
        ModelConfig,
        ProgramConfig,
        Scheme,
        referenced_devices,
    )

    s1 = Scheme(
        config_source="S1",
        required_devices=frozenset({"rtx 4060", "v100"}),
        command=Command(exe="a.bat"),
        memory_mb={"rtx 4060": 5120},
    )
    s2 = Scheme(
        config_source="S2",
        required_devices=frozenset({"780m"}),
        command=Command(exe="b.bat"),
        memory_mb={"780m": 2048, "v100": 0},
    )
    m = ModelConfig(aliases=("m",), mode="Chat", port=1000, schemes={"S1": s1, "S2": s2})
    cfg = AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={"M": m},
        wol=None,
        claude_configs={},
    )
    assert referenced_devices(cfg) == {"rtx 4060", "v100", "780m"}


def test_referenced_devices_empty_when_no_schemes():
    from llm_manager.config import (
        AppConfig,
        ModelConfig,
        ProgramConfig,
        referenced_devices,
    )

    m = ModelConfig(aliases=("m",), mode="Chat", port=1000)
    cfg = AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={"M": m},
        wol=None,
        claude_configs={},
    )
    assert referenced_devices(cfg) == set()


def test_pricing_defaults_to_free_tier():
    from llm_manager.config import ModelConfig

    m = ModelConfig(aliases=("M",), mode="Chat", port=1, auto_start=False, schemes={})
    assert m.pricing.pricing_type == "tier"
    assert m.pricing.hourly_price == 0.0
    assert m.pricing.tiers == ()


def test_validate_rejects_duplicate_tier_index_and_negative_price():
    from llm_manager.config import Pricing, PricingTier

    cfg = AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={
            "M": ModelConfig(
                aliases=("M",),
                mode="Chat",
                port=1,
                auto_start=False,
                schemes={"S": Scheme("S", frozenset({"gpu"}), Command(exe="a.bat"), {"gpu": 1})},
                pricing=Pricing(
                    tiers=(
                        PricingTier(tier_index=1, input_price=-1.0),
                        PricingTier(tier_index=1),
                    )
                ),
            )
        },
        wol=None,
        claude_configs={},
    )
    errs = validate(cfg)
    assert any("duplicate tier_index 1" in e for e in errs)
    assert any("negative price" in e for e in errs)


def _prog(**kw):
    base = {"host": "0.0.0.0", "port": 8080, "alive_time": 60, "log_level": "INFO"}
    base.update(kw)
    return ProgramConfig(**base)


def test_validate_rejects_empty_alias_and_intra_model_duplicate():
    # 同一模型内重复别名 → "duplicate alias"(非跨模型 "shared by")
    cfg = AppConfig(
        program=_prog(),
        models={
            "M": ModelConfig(
                aliases=("dup", "dup", ""),
                mode="Chat",
                port=1,
                auto_start=False,
                schemes={"S": Scheme("S", frozenset({"gpu"}), Command(exe="a.bat"), {"gpu": 1})},
            )
        },
        wol=None,
        claude_configs={},
    )
    errs = validate(cfg)
    assert any("duplicate alias 'dup'" in e for e in errs)
    assert any("empty alias" in e for e in errs)


def test_validate_rejects_out_of_range_ports():
    cfg = AppConfig(
        program=_prog(port=99999),
        models={
            "M": ModelConfig(
                aliases=("m",),
                mode="Chat",
                port=0,
                auto_start=False,
                schemes={"S": Scheme("S", frozenset({"gpu"}), Command(exe="a.bat"), {"gpu": 1})},
            )
        },
        wol=None,
        claude_configs={},
    )
    errs = validate(cfg)
    assert any("Program port 99999 out of range" in e for e in errs)
    assert any("port 0 out of range" in e for e in errs)


# ---------- substitute_vars:启动命令变量替换 ----------


def _model(port: int = 10004, aliases: tuple[str, ...] = ("Qwen3.5-2B", "q")) -> ModelConfig:
    return ModelConfig(
        aliases=aliases,
        mode="Chat",
        port=port,
        auto_start=False,
        schemes={"S": Scheme("S", frozenset({"gpu"}), Command(exe="a.bat"), {"gpu": 1})},
    )


def test_substitute_vars_replaces_port_and_first_alias():
    m = _model(port=10004, aliases=("Qwen3.5-2B", "q"))
    assert substitute_vars("--host 127.0.0.1 --port {{port}}", m) == "--host 127.0.0.1 --port 10004"
    assert substitute_vars("-a {{alias}}", m) == "-a Qwen3.5-2B"
    # 仅第一别名替换,非首个别名不换
    assert substitute_vars("{{alias}} vs q", m) == "Qwen3.5-2B vs q"
    # 组合
    assert substitute_vars("--port {{port}} -a {{alias}}", m) == "--port 10004 -a Qwen3.5-2B"


def test_substitute_vars_without_placeholder_unchanged():
    m = _model()
    assert substitute_vars("--temp 0.8 -c 8192", m) == "--temp 0.8 -c 8192"
    # 写死端口/别名不误伤
    assert substitute_vars("--port 10010", m) == "--port 10010"


def test_substitute_vars_does_not_touch_single_brace_json():
    # 单大括号 JSON 参数(如 --chat-template-kwargs)不得被误认成占位符
    m = _model()
    s = substitute_vars('{"enable_thinking":false}', m)
    assert s == '{"enable_thinking":false}'
    s2 = substitute_vars('--chat-template-kwargs {"enable_thinking":false}', m)
    assert s2 == '--chat-template-kwargs {"enable_thinking":false}'


def test_substitute_vars_empty_aliases_yields_empty_alias():
    m = _model(aliases=())
    assert substitute_vars("-a {{alias}}", m) == "-a "


# ---------- 云端:parse_cloud_id + validate 云块 ----------


def test_parse_cloud_id_basic():
    assert parse_cloud_id("deepseek/deepseek-chat") == ("deepseek", "deepseek-chat")
    assert parse_cloud_id("local-model") is None  # 无 '/'
    assert parse_cloud_id("a/b/c") is None  # 多斜杠 → 404 语义
    assert parse_cloud_id("") is None
    assert parse_cloud_id("/x") is None  # provider 空
    assert parse_cloud_id("x/") is None  # model 空


def _cloud_provider(**kw) -> CloudProvider:
    return CloudProvider(**{"name": "deepseek", **kw})


def test_validate_cloud_provider_name_and_bases():
    cfg = AppConfig(
        program=_prog(),
        models={},
        wol=None,
        claude_configs={},
        cloud_providers={"a/b": _cloud_provider()},
    )
    assert any("must not contain '/'" in e for e in validate(cfg))
    cfg2 = AppConfig(
        program=_prog(),
        models={},
        wol=None,
        claude_configs={},
        cloud_providers={"ds": _cloud_provider(openai_base="ftp://x")},
    )
    assert any("openai_base" in e for e in validate(cfg2))


def test_validate_cloud_model_rules():
    p = _cloud_provider(models=(CloudModel(model_name="m/x"),))
    cfg = AppConfig(
        program=_prog(), models={}, wol=None, claude_configs={}, cloud_providers={"ds": p}
    )
    assert any("model 'm/x'" in e for e in validate(cfg))


def test_validate_cloud_mapping_global_unique_and_reserved():
    p1 = _cloud_provider(
        name="a", mappings=(CloudMapping(local_path="v1/x", target_url="https://a/x"),)
    )
    p2 = _cloud_provider(
        name="b", mappings=(CloudMapping(local_path="v1/x", target_url="https://b/x"),)
    )
    cfg = AppConfig(
        program=_prog(),
        models={},
        wol=None,
        claude_configs={},
        cloud_providers={"a": p1, "b": p2},
    )
    assert any("shared by providers" in e for e in validate(cfg))
    cfg2 = AppConfig(
        program=_prog(),
        models={},
        wol=None,
        claude_configs={},
        cloud_providers={
            "a": _cloud_provider(
                mappings=(CloudMapping(local_path="health", target_url="https://h"),)
            )
        },
    )
    assert any("reserved route" in e for e in validate(cfg2))


def test_validate_local_name_forbids_slash():
    m = ModelConfig(aliases=("m/x",), mode="Chat", port=1)
    cfg = AppConfig(program=_prog(), models={"m": m}, wol=None, claude_configs={})
    assert any("must not contain '/'" in e for e in validate(cfg))


def test_validate_local_name_conflicts_with_provider():
    """本地模型名/别名禁与云服务商名冲突:服务商级用量锚点 {provider} 不含 '/',
    撞名时会与本地锚点共用 models 行 → 成本 local/cloud 拆分串档。"""
    prog = ProgramConfig(host="0.0.0.0", port=8080, alive_time=60, log_level="INFO")
    clash_alias = AppConfig(
        program=prog,
        models={"m": ModelConfig(aliases=("m1", "ds"), mode="Chat", port=1, schemes={})},
        wol=None,
        claude_configs={},
        cloud_providers={"ds": _cloud_provider()},
    )
    errs = [e for e in validate(clash_alias) if "must not equal a cloud provider name" in e]
    assert len(errs) == 1 and "alias 'ds'" in errs[0]

    clash_key = AppConfig(
        program=prog,
        models={"ds": ModelConfig(aliases=("x",), mode="Chat", port=1, schemes={})},
        wol=None,
        claude_configs={},
        cloud_providers={"ds": _cloud_provider()},
    )
    errs2 = [e for e in validate(clash_key) if "must not equal a cloud provider name" in e]
    assert any(e.startswith("Model name 'ds'") for e in errs2)

    clean = AppConfig(
        program=prog,
        models={"other": ModelConfig(aliases=("clean",), mode="Chat", port=1, schemes={})},
        wol=None,
        claude_configs={},
        cloud_providers={"ds": _cloud_provider()},
    )
    assert all("must not equal a cloud provider name" not in e for e in validate(clean))


def test_validate_offpeak_rules():
    """峰谷校验:dual 开 → 谷表非空且至少 1 个时段窗口,分钟 ∈[0,1439],start≠end;
    dual 关不校验(残留数据宽进,读取侧恒忽略)。"""
    from llm_manager.config import PricingTier, TimeWindow

    prog = ProgramConfig(host="0.0.0.0", port=8080, alive_time=60, log_level="INFO")
    base = (PricingTier(tier_index=1, input_price=3.0),)
    off = (PricingTier(tier_index=1, input_price=1.0),)

    def _cfg(cm):
        return AppConfig(
            program=prog,
            models={},
            wol=None,
            claude_configs={},
            cloud_providers={"ds": CloudProvider(name="ds", models=(cm,))},
        )

    def _cm(windows=(), tiers_offpeak=None, dual=True):
        return CloudModel(
            model_name="x",
            dual_pricing=dual,
            offpeak_windows=windows,
            tiers_base=base,
            tiers_offpeak=tiers_offpeak if tiers_offpeak is not None else off,
        )

    assert any("offpeak_windows" in e for e in validate(_cfg(_cm())))  # 无窗口
    no_tiers = _cfg(_cm(windows=(TimeWindow(1380, 300),), tiers_offpeak=()))
    assert any("tiers_offpeak" in e for e in validate(no_tiers))  # 无谷表
    assert any("start_min" in e for e in validate(_cfg(_cm(windows=(TimeWindow(1440, 300),)))))
    assert any("end_min" in e for e in validate(_cfg(_cm(windows=(TimeWindow(100, -1),)))))
    assert any(
        "start_min == end_min" in e or "start != end" in e
        for e in validate(_cfg(_cm(windows=(TimeWindow(360, 360),))))
    )  # 起止相同
    ok_cross = _cfg(_cm(windows=(TimeWindow(1380, 300),)))  # 跨午夜合法
    assert all("offpeak" not in e and "window" not in e.lower() for e in validate(ok_cross))
    ok_dual_off = _cfg(_cm(windows=(TimeWindow(9999, 9999),), dual=False))
    assert all("must be within" not in e for e in validate(ok_dual_off))  # dual 关:越界数据不校验
