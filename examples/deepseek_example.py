"""DeepSeek roleplay bot example: the most common use case in the Chinese
LLM community — a QQ/WeChat bot with a fixed persona, Chinese dialogue, and
fullwidth-parens stage directions（像这样）.

Two ways to integrate (choose one):

  A. Library mode (this file)
     pip install roleplay-slim
     from roleplay_slim import compress, CompressorConfig
     Call compress() before sending to DeepSeek.

  B. Proxy mode (zero code changes — see docstring at the bottom)
     pip install "roleplay-slim[all]"
     roleplay-slim-proxy --config examples/example_config.toml
     Point your app at http://127.0.0.1:8791/v1

This file demonstrates library mode with a dry-run conversation. No API key
needed — just run it and see compression in action:

    python examples/deepseek_example.py
"""

from roleplay_slim import CompressorConfig, compress

# ---------------------------------------------------------------------------
# Config — matching a typical QQ bot's message structure
# ---------------------------------------------------------------------------
CONFIG = CompressorConfig(
    keep_recent_turns=2,
    enable_strip_stage_directions=True,
    stage_direction_pattern="fullwidth_parens",    # （动作描写）
    enable_dedupe_verbatim_tail=True,
)

# ---------------------------------------------------------------------------
# Persona (fixed system prompt — will be auto-detected as cache-safe prefix)
# ---------------------------------------------------------------------------
PERSONA = (
    "你是「寒江」，一位独居在雪山脚下的中年剑客。说话简短、不废话，"
    "偶尔蹦出一两句江湖老话。对陌生人冷淡，对朋友话多三成。"
    "绝不使用 emoji 和网络用语。"
)

# ---------------------------------------------------------------------------
# A 6-turn conversation with stage directions
# ---------------------------------------------------------------------------
messages: list[dict] = [
    {"role": "system", "content": PERSONA},
    {"role": "user", "content": "（裹紧斗篷，在风雪中喊）前辈——能借个火吗？"},
    {"role": "assistant", "content": "（头也不抬，往火堆里扔了根柴）门没锁。自己进来。"},
    {"role": "user", "content": "（抖掉肩上的雪）这鬼天气，山下的客栈都关门了。"},
    {"role": "assistant", "content": "（嗤笑一声）开也不顶用。这雪还要下三天。"},
    {"role": "user", "content": "（从包袱里摸出酒囊）带了点烧刀子……喝一口？"},
    {"role": "assistant", "content": "（终于抬眼看了一下）搁那儿。坐。"},
    {"role": "user", "content": "（坐下，搓着手）前辈在这儿住了多久？"},
    {"role": "assistant", "content": "（端起碗喝了口茶）七年。没人问过我这话。"},
]

# Repeat a format reminder as the footer (simulates a real QQ bot's per-turn footer)
FOOTER = {"role": "system", "content": "[FORMAT] 保持简短，不超过两句话。"}
messages_with_footer = messages + [FOOTER]

# ---------------------------------------------------------------------------
# Dry-run: compress and show results
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    compressed = compress(messages_with_footer, CONFIG)

    before_chars = sum(
        len(m["content"]) for m in messages_with_footer
        if isinstance(m["content"], str)
    )
    after_chars = sum(
        len(m["content"]) for m in compressed
        if isinstance(m["content"], str)
    )

    print("=== DeepSeek Roleplay Bot — Compression Dry-Run ===\n")
    print(f"Messages before: {len(messages_with_footer)}")
    print(f"Messages after:  {len(compressed)}")
    print(f"Chars: {before_chars} -> {after_chars} "
          f"(saved {before_chars - after_chars}, "
          f"{(before_chars - after_chars) / before_chars * 100:.1f}%)\n")

    # Verify persona untouched
    prefix_msg = compressed[0]
    assert prefix_msg["content"] == PERSONA, (
        "PERSONA PREFIX WAS MODIFIED — this is a bug"
    )
    print("[OK] Persona prefix preserved byte-for-byte")

    print("\nRun with a real DeepSeek key via proxy mode: see docstring.")


# ---------------------------------------------------------------------------
# Proxy mode (optional — zero code changes)
# ---------------------------------------------------------------------------
#
#   $env:UPSTREAM_API_KEY = "sk-your-deepseek-key"
#   roleplay-slim-proxy --config examples/example_config.toml
#
# Then in your QQ bot code:
#
#   from openai import OpenAI
#   client = OpenAI(
#       base_url="http://127.0.0.1:8791/v1",
#       api_key="not-used",
#   )
#   response = client.chat.completions.create(
#       model="deepseek-chat",
#       messages=messages,
#   )
#
# Every request prints:
#   [roleplay-slim] request #1 | 1204 -> 891 tokens (saved 313, 26.0%)
