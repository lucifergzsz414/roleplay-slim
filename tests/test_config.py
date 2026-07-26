import pytest

from roleplay_slim.config import CompressorConfig, STAGE_DIRECTION_PRESETS


def test_default_config_constructs_without_error():
    config = CompressorConfig()
    assert config.keep_recent_turns == 3


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
