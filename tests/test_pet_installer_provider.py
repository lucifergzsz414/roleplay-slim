from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def _load_installer():
    path = ROOT / "integrations/pet-installer/installer/install.py"
    spec = importlib.util.spec_from_file_location("mutsumi_provider_installer", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provider_presets_include_deepseek_bailian_and_custom():
    installer = _load_installer()

    assert installer.PROVIDER_PRESETS["DeepSeek"]["base_url"] == (
        "https://api.deepseek.com/v1"
    )
    assert installer.PROVIDER_PRESETS["阿里云百炼"]["model"] == "qwen3.6-plus"
    assert installer.PROVIDER_PRESETS["自定义 OpenAI 兼容"]["base_url"] == ""


def test_render_proxy_config_supports_provider_and_optional_model():
    installer = _load_installer()

    rendered = installer.render_proxy_config(
        8791,
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen3.6-plus",
    )
    parsed = tomllib.loads(rendered)

    assert parsed["proxy"]["upstream_base_url"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert parsed["proxy"]["upstream_model"] == "qwen3.6-plus"
    assert parsed["proxy"]["port"] == 8791

    passthrough = tomllib.loads(
        installer.render_proxy_config(8791, "https://api.deepseek.com/v1", "")
    )
    assert "upstream_model" not in passthrough["proxy"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com/v1/", "https://example.com/v1"),
        ("http://127.0.0.1:8787/v1/", "http://127.0.0.1:8787/v1"),
        ("http://localhost:8787/v1", "http://localhost:8787/v1"),
    ],
)
def test_normalize_upstream_base_url_accepts_secure_or_local_urls(raw, expected):
    installer = _load_installer()

    assert installer.normalize_upstream_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "example.com/v1",
        "http://example.com/v1",
        "ftp://example.com/v1",
        "https://example.com/v1?key=secret",
        "https://user:password@example.com/v1",
    ],
)
def test_normalize_upstream_base_url_rejects_unsafe_or_ambiguous_urls(raw):
    installer = _load_installer()

    with pytest.raises(ValueError):
        installer.normalize_upstream_base_url(raw)
