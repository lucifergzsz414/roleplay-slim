"""Tests for the `roleplay-slim preview` command.

The command's job is to tell the truth about a lossy transform before
someone enables it on production prompts, so most of what's checked here
is that it reports accurately and refuses to crash on messy input.
"""
from __future__ import annotations

import io
import json

import pytest

from roleplay_slim.cli import main

PREFIX = {"role": "system", "content": "You are a character. " * 5}
FOOTER = {"role": "system", "content": "[FORMAT] end with a tag"}


def _conversation(n_turns: int = 8) -> list[dict]:
    messages = [dict(PREFIX)]
    for i in range(n_turns):
        messages.append(
            {"role": "user", "content": f"Question {i}. Middle part. Final part."}
        )
        messages.append(
            {"role": "assistant", "content": f"Answer {i}. Middle part. Final part."}
        )
        messages.append(dict(FOOTER))
    return messages


def _write_json(tmp_path, payload, name="convo.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _run(*argv) -> tuple[int, str]:
    out = io.StringIO()
    code = main(list(argv), out=out)
    return code, out.getvalue()


# --- input handling -----------------------------------------------------


def test_accepts_a_bare_messages_array(tmp_path):
    path = _write_json(tmp_path, _conversation())
    code, text = _run("preview", path)
    assert code == 0
    assert "roleplay-slim preview" in text


def test_accepts_a_full_request_body(tmp_path):
    """A payload captured straight off the wire should work unedited."""
    path = _write_json(tmp_path, {"model": "x", "messages": _conversation()})
    code, text = _run("preview", path)
    assert code == 0
    assert "resulting prompt" in text


def test_reads_from_stdin(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_conversation())))
    code, text = _run("preview", "-")
    assert code == 0
    assert "resulting prompt" in text


def test_missing_file_is_a_clean_error_not_a_traceback(tmp_path):
    with pytest.raises(SystemExit) as e:
        _run("preview", str(tmp_path / "nope.json"))
    assert "no such file" in str(e.value)


def test_malformed_json_is_a_clean_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        _run("preview", str(path))
    assert "not valid JSON" in str(e.value)


def test_wrong_shape_is_a_clean_error(tmp_path):
    path = _write_json(tmp_path, {"model": "x"})  # no messages key
    with pytest.raises(SystemExit) as e:
        _run("preview", path)
    assert "expected a JSON array of messages" in str(e.value)


def test_directory_argument_is_a_clean_error(tmp_path):
    with pytest.raises(SystemExit) as e:
        _run("preview", str(tmp_path))
    assert "error:" in str(e.value)


# --- reporting ----------------------------------------------------------


def test_reports_savings(tmp_path):
    path = _write_json(tmp_path, _conversation())
    _, text = _run("preview", path, "--keep-recent-turns", "2")
    assert "saved" in text
    assert "messages" in text


def test_prefix_is_reported_as_untouched(tmp_path):
    """The line that matters most for trust."""
    path = _write_json(tmp_path, _conversation())
    _, text = _run("preview", path)
    assert "passed through unchanged" in text
    assert "[prefix]" in text


def test_no_prefix_case_is_stated_explicitly(tmp_path):
    """Silence would read as "prefix protected"; it isn't, there isn't one."""
    path = _write_json(
        tmp_path,
        [{"role": "user", "content": "hi there. second sentence. third one."}],
    )
    _, text = _run("preview", path)
    assert "none detected" in text


def test_warns_when_prefix_alone_exceeds_the_budget(tmp_path):
    path = _write_json(tmp_path, _conversation())
    _, text = _run("preview", path, "--max-prompt-tokens", "5")
    assert "WARNING" in text
    assert "cannot be met" in text


def test_dropped_content_is_listed(tmp_path):
    path = _write_json(tmp_path, _conversation(10))
    _, text = _run("preview", path, "--mode", "drop", "--keep-recent-turns", "1")
    assert "gone from the original" in text
    assert "Question 0." in text


def test_nothing_dropped_is_stated_explicitly(tmp_path):
    path = _write_json(tmp_path, _conversation(2))
    _, text = _run("preview", path, "--keep-recent-turns", "10")
    assert "every original message survives verbatim" in text


def test_repeated_dropped_content_is_collapsed_with_a_count(tmp_path):
    """Identical old messages collapse to one line with a count so a long
    history stays readable. The repeated text must not also appear in the
    surviving window — content that survives anywhere is not "gone"."""
    messages = [dict(PREFIX)]
    for i in range(6):
        messages.append({"role": "user", "content": f"Q{i}. Two. Three."})
        messages.append({"role": "assistant", "content": "Same answer. Two. Three."})
    messages.append({"role": "user", "content": "Final question. Two. Three."})
    messages.append({"role": "assistant", "content": "Distinct final answer. Two."})

    path = _write_json(tmp_path, messages)
    _, text = _run("preview", path, "--mode", "drop", "--keep-recent-turns", "1")
    assert "(x6)" in text


def test_quiet_suppresses_detail_but_keeps_the_summary(tmp_path):
    path = _write_json(tmp_path, _conversation())
    _, text = _run("preview", path, "--quiet")
    assert "saved" in text
    assert "resulting prompt" not in text


# --- machine-readable output --------------------------------------------


def test_json_mode_emits_the_compressed_messages(tmp_path):
    path = _write_json(tmp_path, _conversation())
    code, text = _run("preview", path, "--json", "--keep-recent-turns", "2")
    assert code == 0
    parsed = json.loads(text)
    assert isinstance(parsed, list)
    assert parsed[0]["content"] == PREFIX["content"]


def test_json_mode_output_is_shorter_than_input(tmp_path):
    messages = _conversation(12)
    path = _write_json(tmp_path, messages)
    _, text = _run("preview", path, "--json", "--mode", "drop", "--keep-recent-turns", "2")
    assert len(json.loads(text)) < len(messages)


# --- config plumbing ----------------------------------------------------


def test_config_file_is_honoured(tmp_path):
    config = tmp_path / "c.toml"
    config.write_text(
        '[compressor]\nkeep_recent_turns = 1\nhistory_window_mode = "drop"\n',
        encoding="utf-8",
    )
    path = _write_json(tmp_path, _conversation(10))
    _, text = _run("preview", path, "--config", str(config), "--json")
    assert len(json.loads(text)) < 10


def test_cli_flag_overrides_the_config_file(tmp_path):
    config = tmp_path / "c.toml"
    config.write_text("[compressor]\nkeep_recent_turns = 8\n", encoding="utf-8")
    path = _write_json(tmp_path, _conversation(10))

    _, wide = _run("preview", path, "--config", str(config), "--json")
    _, narrow = _run(
        "preview", path, "--config", str(config), "--keep-recent-turns", "1",
        "--mode", "drop", "--json",
    )
    assert len(json.loads(narrow)) < len(json.loads(wide))


def test_invalid_override_is_rejected_by_config_validation(tmp_path):
    """Overrides go through the same validation as a config file, rather
    than reaching compression as a silently bad value."""
    path = _write_json(tmp_path, _conversation())
    with pytest.raises(ValueError, match="max_prompt_tokens"):
        _run("preview", path, "--max-prompt-tokens", "0")


# --- resilience ---------------------------------------------------------


def test_survives_multimodal_and_tool_messages(tmp_path):
    """Content isn't always a string — the describer must not crash on the
    vision-format list, a null content with tool_calls, or a tool result."""
    messages = [
        dict(PREFIX),
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "look", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "a cat"},
        {"role": "assistant", "content": "A cat. Definitely. Yes."},
        {"role": "user", "content": "thanks. bye. see you."},
    ]
    path = _write_json(tmp_path, messages)
    code, text = _run("preview", path, "--keep-recent-turns", "1")
    assert code == 0
    assert "multimodal part(s)" in text
    assert "tool_call(s)" in text


def test_handles_cjk_content_on_a_legacy_console(tmp_path):
    """Windows terminals often run a code page that can't encode CJK. The
    preview must degrade characters, not die partway through."""
    class LegacyConsole(io.StringIO):
        encoding = "gbk"

        def write(self, s):
            if any(ord(c) > 0x2000 and not 0x4E00 <= ord(c) <= 0x9FFF for c in s):
                raise UnicodeEncodeError("gbk", s, 0, 1, "unsupported")
            return super().write(s)

    messages = [dict(PREFIX)]
    for i in range(6):
        messages.append({"role": "user", "content": f"在吗{i}。第二句。第三句。"})
        messages.append({"role": "assistant", "content": f"嗯我在{i}。第二句。第三句。"})
    path = _write_json(tmp_path, messages)

    console = LegacyConsole()
    assert main(["preview", path, "--keep-recent-turns", "1"], out=console) == 0
    assert "resulting prompt" in console.getvalue()


def test_empty_conversation_does_not_crash(tmp_path):
    path = _write_json(tmp_path, [])
    code, text = _run("preview", path)
    assert code == 0
    assert "none detected" in text


def test_prefix_only_conversation_does_not_crash(tmp_path):
    path = _write_json(tmp_path, [dict(PREFIX)])
    code, text = _run("preview", path)
    assert code == 0
    assert "[prefix]" in text


def test_subcommand_is_required():
    with pytest.raises(SystemExit):
        main([])


# --- optimize -----------------------------------------------------------

def _optimize_samples(n: int = 20) -> list[list[dict]]:
    """Samples with a stable 1-message prefix and varying first user line."""
    return [
        [dict(PREFIX), {"role": "user", "content": f"Q{i}. Two. Three."}]
        for i in range(n)
    ]


def test_optimize_reports_stable_prefix(tmp_path):
    path = _write_json(tmp_path, _optimize_samples(), name="samples.json")
    code, text = _run("optimize", path)
    assert code == 0
    assert "roleplay-slim optimize" in text
    assert "recommended prefix: first 1 message(s)" in text
    assert "cache-hit ceiling estimate" in text


def test_optimize_accepts_request_body_samples(tmp_path):
    samples = [
        {"model": "x", "messages": [dict(PREFIX), {"role": "user", "content": f"Q{i}. Two."}]}
        for i in range(20)
    ]
    path = _write_json(tmp_path, samples)
    code, text = _run("optimize", path)
    assert code == 0


def test_optimize_json_mode(tmp_path):
    path = _write_json(tmp_path, _optimize_samples())
    code, text = _run("optimize", path, "--json")
    assert code == 0
    parsed = json.loads(text)
    assert parsed["samples"] == 20
    assert parsed["recommended_prefix"] == 1
    assert len(parsed["positions"]) >= 2
    assert parsed["positions"][0]["stable"] is True


def test_optimize_empty_samples_is_clean_error(tmp_path):
    path = _write_json(tmp_path, [])
    with pytest.raises(SystemExit) as e:
        _run("optimize", path)
    assert "at least one sample" in str(e.value)


def test_optimize_malformed_sample_is_clean_error(tmp_path):
    path = _write_json(tmp_path, [[dict(PREFIX), "not a message dict"]])
    with pytest.raises(SystemExit) as e:
        _run("optimize", path)
    assert "sample #0" in str(e.value)
