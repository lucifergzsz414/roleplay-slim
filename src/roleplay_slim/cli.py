"""Command-line entry point: `roleplay-slim <subcommand>`.

The `preview` subcommand exists because this library performs a *lossy*
transform on somebody's production prompts. Config alone doesn't tell you
what a given setting will actually do to your conversations — whether
"trim" touches your messages at all (it does nothing to text without
sentence-ending punctuation), how much of the prompt your prefix already
occupies, or which turns a token budget will drop. Being able to see the
answer on real data before enabling anything is the difference between
trusting the compressor and hoping.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, TextIO

from .compressor import compress
from .config import CompressorConfig
from .segmenter import segment
from .stats import estimate_messages_tokens
from .strategies import content_key

# Longest message body shown per line before eliding. Wide enough to
# recognise a message, short enough that a 200-turn history stays readable.
_SNIPPET = 68


def _write(stream: TextIO, text: str) -> None:
    """Print a line, surviving a console that can't encode it.

    Windows terminals frequently run a legacy code page (GBK, cp1252) that
    cannot represent the CJK text this project exists to compress. Losing
    a few characters to '?' is an acceptable preview; crashing with
    UnicodeEncodeError partway through is not.
    """
    try:
        stream.write(text + "\n")
        return
    except UnicodeEncodeError:
        pass

    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        stream.write(text.encode(encoding, errors="replace").decode(encoding) + "\n")
        return
    except UnicodeEncodeError:
        # A stream whose declared encoding and actual capability disagree.
        # ASCII is the floor: the report's structure (counts, markers,
        # section headings) is all ASCII anyway, so this degrades the
        # message snippets and keeps everything that carries the verdict.
        pass

    stream.write(text.encode("ascii", errors="replace").decode("ascii") + "\n")


def _load_messages(source: str) -> list[dict]:
    """Read messages from a file path, or from stdin when source is "-".

    Accepts either a full OpenAI request body ({"messages": [...]}, which
    is what you get by dumping a real request) or a bare array, so a
    captured payload can be fed straight in without editing.
    """
    if source == "-":
        raw = sys.stdin.read()
    else:
        try:
            with open(source, encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            raise SystemExit(f"error: no such file: {source}")
        except IsADirectoryError:
            raise SystemExit(f"error: {source} is a directory, not a JSON file")
        except UnicodeDecodeError as e:
            raise SystemExit(f"error: {source} is not UTF-8 text ({e})")
        except OSError as e:
            raise SystemExit(f"error: could not read {source} ({e})")

    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"error: {source} is not valid JSON ({e})")

    if isinstance(data, dict):
        data = data.get("messages")
    if not isinstance(data, list) or not all(isinstance(m, dict) for m in data):
        raise SystemExit(
            f"error: expected a JSON array of messages, or an object with a "
            f'"messages" array — got {type(data).__name__} in {source}'
        )
    return data


def _describe(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        body = f"<{len(content)} multimodal part(s)>"
    elif content is None:
        calls = message.get("tool_calls") or []
        body = f"<{len(calls)} tool_call(s)>" if calls else "<empty>"
    else:
        body = " ".join(str(content).split())
    if len(body) > _SNIPPET:
        body = body[: _SNIPPET - 1] + "…"
    return f"{message.get('role', '?'):<9} {body}"


def _content_id(message: dict) -> tuple:
    """Identity of a message's payload: role plus content.

    Used for set membership ("does this exact message survive?"), not for
    pairing an original with its replacement — see _render_report.
    """
    return (message.get("role"), content_key(message.get("content", "")))


def _preview(args: argparse.Namespace, out: TextIO) -> int:
    messages = _load_messages(args.input)

    if args.config:
        config = CompressorConfig.from_toml(args.config)
    else:
        config = CompressorConfig()
    if args.max_prompt_tokens is not None:
        config.max_prompt_tokens = args.max_prompt_tokens
    if args.keep_recent_turns is not None:
        config.keep_recent_turns = args.keep_recent_turns
    if args.mode is not None:
        config.history_window_mode = args.mode
    # Re-run validation after the CLI overrides, so a bad combination is
    # reported the same way it would be if it came from the config file.
    config.__post_init__()

    compressed = compress(messages, config)

    if args.json:
        json.dump(compressed, out, ensure_ascii=False, indent=2)
        out.write("\n")
        return 0

    before = estimate_messages_tokens(messages)
    after = estimate_messages_tokens(compressed)
    saved = before - after
    pct = (saved / before * 100) if before else 0.0

    prefix, turns = segment(messages, config.prefix_override)
    prefix_tokens = estimate_messages_tokens(prefix)

    _write(out, "=" * 72)
    _write(out, "roleplay-slim preview")
    _write(out, "=" * 72)
    _write(
        out,
        f"  messages   {len(messages):>6}  ->{len(compressed):>6}",
    )
    _write(
        out,
        f"  tokens~    {before:>6}  ->{after:>6}   saved {saved} ({pct:.1f}%)",
    )
    _write(out, f"  turns      {len(turns):>6}")
    _write(out, "")

    # The single most important line for trust: the prefix is the part a
    # provider's cache rewards for staying identical, and this tool exists
    # partly to show that it isn't being touched.
    if prefix:
        share = (prefix_tokens / before * 100) if before else 0.0
        _write(
            out,
            f"  prefix     {len(prefix)} message(s), ~{prefix_tokens} tokens "
            f"({share:.0f}% of the prompt) — passed through unchanged",
        )
        if config.max_prompt_tokens is not None and prefix_tokens >= config.max_prompt_tokens:
            _write(
                out,
                f"             WARNING: prefix alone meets or exceeds the "
                f"{config.max_prompt_tokens}-token budget, which therefore "
                f"cannot be met",
            )
    else:
        _write(
            out,
            "  prefix     none detected — no leading system messages, so the "
            "whole prompt is treated as compressible",
        )
    _write(out, "")

    if not args.quiet:
        _render_report(messages, compressed, len(prefix), out)

    return 0


def _render_report(before: list[dict], after: list[dict], n_prefix: int, out: TextIO) -> None:
    """Show the prompt that would actually be sent, then what fell out of it.

    Note what this deliberately does *not* do: claim a one-to-one mapping
    between original and compressed messages. Earlier attempts aligned the
    two lists (by content, then by role) and both produced confident
    nonsense — content-based keys leave a rewritten message matching
    nothing, and role-based keys are so coarse that the alignment is
    ambiguous and difflib anchors on the wrong repeat. There genuinely is
    no reliable pairing to report: "trim" rewrites a message in place,
    "summarize" replaces a whole block with one new message, and dedupe
    removes copies from arbitrary positions.

    So this reports only what can be established without guessing: whether
    a given piece of content survives verbatim, which is exact set
    membership, and what the resulting prompt looks like — which is the
    thing being previewed in the first place.
    """
    surviving = {_content_id(m) for m in after}
    original_ids = {_content_id(m) for m in before}

    _write(out, "-" * 72)
    _write(out, f"resulting prompt ({len(after)} messages)")
    _write(out, "-" * 72)
    for j, message in enumerate(after):
        if j < n_prefix:
            _write(out, f"=   {_describe(message)}   [prefix]")
        elif _content_id(message) in original_ids:
            _write(out, f"= {_describe(message)}")
        else:
            _write(out, f"+ {_describe(message)}")

    dropped = [m for m in before if _content_id(m) not in surviving]
    _write(out, "")
    _write(out, "-" * 72)
    _write(out, f"gone from the original ({len(dropped)} messages)")
    _write(out, "-" * 72)
    if not dropped:
        _write(out, "  nothing — every original message survives verbatim")
    else:
        # Identical messages (a footer repeated every turn) collapse into
        # one line with a count, so a long history stays readable.
        seen: dict[tuple, int] = {}
        order: list[dict] = []
        for message in dropped:
            key = _content_id(message)
            if key in seen:
                seen[key] += 1
            else:
                seen[key] = 1
                order.append(message)
        for message in order:
            n = seen[_content_id(message)]
            suffix = f"   (x{n})" if n > 1 else ""
            _write(out, f"- {_describe(message)}{suffix}")

    _write(out, "")
    _write(out, "  = content carried over verbatim   + new text produced by compression")
    _write(out, "  - content not present in the result")
    _write(
        out,
        "  Deduplicated repeats are not listed as gone: one copy survives, so "
        "the content did.",
    )
    _write(
        out,
        "  Rewritten messages appear as a '-' in the second list and a '+' in "
        "the first;",
    )
    _write(out, "  no original-to-replacement pairing is claimed, because none is reliable.")


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(
        prog="roleplay-slim",
        description="Dialogue-aware LLM context compression.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    preview = sub.add_parser(
        "preview",
        help="Show what compression would do to a conversation, without sending anything",
        description=(
            "Compress a captured conversation locally and report what changed. "
            "Makes no network requests and writes no files."
        ),
    )
    preview.add_argument(
        "input",
        help='JSON file holding a messages array or a full request body; "-" for stdin',
    )
    preview.add_argument("--config", default=None, help="Path to a TOML config file")
    preview.add_argument(
        "--max-prompt-tokens",
        dest="max_prompt_tokens",
        type=int,
        default=None,
        help="Override max_prompt_tokens for this run",
    )
    preview.add_argument(
        "--keep-recent-turns",
        dest="keep_recent_turns",
        type=int,
        default=None,
        help="Override keep_recent_turns for this run",
    )
    preview.add_argument(
        "--mode",
        choices=("trim", "drop"),
        default=None,
        help='Override history_window_mode ("summarize" needs a Python callable)',
    )
    preview.add_argument(
        "--json",
        action="store_true",
        help="Emit the compressed messages as JSON instead of a report",
    )
    preview.add_argument(
        "--quiet", action="store_true", help="Summary only, no per-message detail"
    )

    args = parser.parse_args(argv)
    if args.command == "preview":
        return _preview(args, out)
    return 1  # pragma: no cover - argparse rejects unknown subcommands first


if __name__ == "__main__":
    raise SystemExit(main())
