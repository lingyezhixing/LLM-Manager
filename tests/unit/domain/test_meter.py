from llm_manager.domain.meter import TokenUsage


def test_token_usage_fields_named():
    u = TokenUsage(input_tokens=10, output_tokens=5, cache_n=3, prompt_n=7)
    assert u.input_tokens == 10
    assert u.output_tokens == 5
    assert u.cache_n == 3
    assert u.prompt_n == 7


def test_is_zero_true_when_all_zero():
    assert TokenUsage(0, 0, 0, 0).is_zero()


def test_is_zero_false_otherwise():
    assert not TokenUsage(1, 0, 0, 0).is_zero()
