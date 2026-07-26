import pytest

from roleplay_slim.config import CompressorConfig, ProxyConfig, STAGE_DIRECTION_PRESETS


def test_default_config_constructs_without_error():
    config = CompressorConfig()
    assert config.keep_recent_turns == 6


def test_negative_keep_recent_turns_rejected():
    with pytest.raises(ValueError, match="keep_recent_turns"):
        CompressorConfig(keep_recent_turns=-1)


def test_invalid_history_window_mode_rejected():
    with pytest.raises(ValueError, match="history_window_mode"):
        CompressorConfig(history_window_mode="summarize")


def test_negative_prefix_override_rejected():
    with pytest.raises(ValueError, match="prefix_override"):
        CompressorConfig(prefix_override=-1)


def test_invalid_regex_pattern_rejected_with_clear_message():
    with pytest.raises(ValueError, match="not a valid regex"):
        CompressorConfig(stage_direction_pattern="(unclosed")


def test_stage_direction_preset_name_resolves_to_its_regex():
    config = CompressorConfig(stage_direction_pattern="asterisk")
    assert config.stage_direction_pattern == STAGE_DIRECTION_PRESETS["asterisk"]


def test_raw_regex_passes_through_unchanged_when_not_a_preset_name():
    raw = r"\{[^}]*\}"
    config = CompressorConfig(stage_direction_pattern=raw)
    assert config.stage_direction_pattern == raw


def test_stage_direction_pattern_is_precompiled_once():
    config = CompressorConfig(stage_direction_pattern="asterisk")
    compiled = config._compiled_stage_direction_pattern
    assert compiled.pattern == STAGE_DIRECTION_PRESETS["asterisk"]
    assert compiled.match("*waves*")


def test_all_presets_are_valid_regexes():
    import re

    for name, pattern in STAGE_DIRECTION_PRESETS.items():
        re.compile(pattern)  # must not raise


def test_prefix_normalize_disabled_by_default():
    config = CompressorConfig()
    assert config.enable_prefix_normalize is False
    assert config.prefix_timestamp_bucket_minutes == 5


def test_prefix_timestamp_bucket_minutes_zero_rejected():
    with pytest.raises(ValueError, match="prefix_timestamp_bucket_minutes"):
        CompressorConfig(prefix_timestamp_bucket_minutes=0)


def test_prefix_timestamp_bucket_minutes_over_60_rejected():
    with pytest.raises(ValueError, match="prefix_timestamp_bucket_minutes"):
        CompressorConfig(prefix_timestamp_bucket_minutes=61)


def test_from_dict_rejects_unknown_compressor_keys():
    with pytest.raises(ValueError, match="unknown compressor config key"):
        CompressorConfig.from_dict({"compressor": {"keep_recent_turns": 3, "typoed_key": True}})


def test_from_dict_accepts_known_compressor_keys():
    config = CompressorConfig.from_dict({"compressor": {"keep_recent_turns": 5}})
    assert config.keep_recent_turns == 5


def test_proxy_from_toml_rejects_unknown_proxy_keys(tmp_path):
    toml_path = tmp_path / "test.toml"
    toml_path.write_text(
        "[proxy]\nupstream_base_url = 'https://example.com/v1'\nclient_auth_token_en = 'TOKEN'\n\n"
        "[compressor]\nkeep_recent_turns = 3\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown proxy config key"):
        ProxyConfig.from_toml(str(toml_path))


def test_proxy_from_toml_rejects_unknown_compressor_keys(tmp_path):
    toml_path = tmp_path / "test.toml"
    toml_path.write_text(
        "[proxy]\nupstream_base_url = 'https://example.com/v1'\n\n"
        "[compressor]\nkeep_recent_turns = 3\nunknown_field = 42\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown compressor config key"):
        ProxyConfig.from_toml(str(toml_path))
