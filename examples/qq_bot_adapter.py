"""QQ bot adapter pattern: how to integrate roleplay-slim into a
NapCat / OneBot / mirai-style QQ bot's message pipeline.

The key insight for QQ bots is that the message array typically has:

  [0] SYSTEM_PROMPT       ← cache-stable persona (never touched by slim)
  [1] SHARED_BLOCK         ← shared per-request context (never touched)
  [2+] history + footers   ← the part slim compresses

roleplay-slim's default heuristic auto-detects this split — the two
leading system messages pass through byte-for-byte, and compression
only runs on what follows.

Integration pattern (pseudocode for your actual bot):

  from roleplay_slim import compress, CompressorConfig

  slim_config = CompressorConfig(
      keep_recent_turns=5,
      enable_strip_stage_directions=True,
      stage_direction_pattern="fullwidth_parens",
  )

  async def build_reply(session_messages: list[dict]) -> str:
      # 1. Run your own memory / fact-extraction layer FIRST
      #    (profile lookup, FTS5 search, etc.) and inject results
      #    into session_messages as needed.
      #
      # 2. Compress
      compressed = compress(session_messages, slim_config)
      #
      # 3. Send to LLM
      response = await llm_call(compressed)
      return response

Proxy mode is even simpler — no code changes at all:

  1. Start the proxy:
     export UPSTREAM_API_KEY=sk-...
     roleplay-slim-proxy --config examples/example_config.toml

  2. In your bot config, swap:
     deepseek_base = "http://127.0.0.1:8791/v1"

  3. Every request is compressed automatically. Poll GET /stats
     for cumulative token savings.

See examples/example_config.toml for a config modelled on a real
production QQ bot's message structure."""


# ---------------------------------------------------------------------------
# Dry-run demo — run this file directly to verify compression behaviour
# on a QQ-bot-style message array without a real bot
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from roleplay_slim import compress, CompressorConfig

    # Simulate a realistic QQ bot message array:
    #   [0] persona, [1] shared block, then user/assistant turns with
    #   repeated footer system messages
    PERSONA = (
        "你是小睦，一个温柔内向的女高中生吉他手。说话轻声细语，"
        "偶尔用省略号代替完整句子。不要主动提及自己是AI。"
    )
    SHARED = "[会话上下文 — 以下内容不属于角色扮演]\n今天是2026年7月26日，天气晴。"

    messages: list[dict] = [
        {"role": "system", "content": PERSONA},
        {"role": "system", "content": SHARED},
    ]

    for i in range(10):
        messages.append({"role": "user", "content": f"（第{i+1}轮用户消息）今天排练怎么样？"})
        messages.append({"role": "assistant", "content": f"（拨了一下琴弦）还行……有个和弦一直弹不好。"})
        messages.append({"role": "system", "content": "[格式规则] 回复末尾加心情标签。"})

    config = CompressorConfig(
        keep_recent_turns=5,
        enable_strip_stage_directions=True,
        stage_direction_pattern="fullwidth_parens",
    )

    compressed = compress(messages, config)

    before_chars = sum(len(m["content"]) for m in messages if isinstance(m["content"], str))
    after_chars = sum(len(m["content"]) for m in compressed if isinstance(m["content"], str))
    print(f"QQ bot dry-run: {len(messages)} messages → {len(compressed)} messages")
    print(f"  {before_chars} → {after_chars} chars "
          f"(saved {before_chars - after_chars}, {(before_chars - after_chars) / before_chars * 100:.1f}%)")

    # Verify prefix untouched
    assert compressed[0]["content"] == PERSONA, "Prefix was modified!"
    assert compressed[1]["content"] == SHARED, "Shared block was modified!"
    print("  [OK] persona prefix preserved byte-for-byte")
