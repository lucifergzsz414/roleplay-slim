"""Discord bot example: wire roleplay-slim into a discord.py bot's
message pipeline. Demonstrates the library approach — import and call
compress() before sending to the LLM.

Setup:
  1. pip install discord.py openai "roleplay-slim[all]"
  2. Set DISCORD_TOKEN and UPSTREAM_API_KEY environment variables.
  3. python discord_bot.py

This is a minimal skeleton — add your own persona, intents, and error
handling for production use."""

import os

from openai import OpenAI

from roleplay_slim import CompressorConfig, compress

# ---------------------------------------------------------------------------
# Config — move these to a config file or env vars in production
# ---------------------------------------------------------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
UPSTREAM_API_KEY = os.getenv("UPSTREAM_API_KEY", "sk-your-key")
LLM_MODEL = "deepseek-chat"       # or any OpenAI-compatible model
LLM_BASE_URL = "https://api.deepseek.com/v1"

# Compression: keep the last 4 turns verbatim, strip stage directions from
# older turns. Tune these per your bot's message style.
COMPRESSOR_CONFIG = CompressorConfig(
    keep_recent_turns=2,
    enable_strip_stage_directions=True,
    stage_direction_pattern="fullwidth_parens",  # （动作描写）
)

# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------
client = OpenAI(base_url=LLM_BASE_URL, api_key=UPSTREAM_API_KEY)


def chat_with_character(messages: list[dict]) -> str:
    """Compress history, then call the LLM."""
    compressed = compress(messages, COMPRESSOR_CONFIG)
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=compressed,
        max_tokens=512,
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Discord bot (minimal — no event loop boilerplate here, import in your
# real bot.py)
# ---------------------------------------------------------------------------
# import discord
#
# class CharacterBot(discord.Client):
#     def __init__(self, persona: str):
#         super().__init__(intents=discord.Intents.default())
#         self.history: list[dict] = [
#             {"role": "system", "content": persona},
#         ]
#
#     async def on_ready(self):
#         print(f"Logged in as {self.user}")
#
#     async def on_message(self, message):
#         if message.author == self.user:
#             return
#         self.history.append({"role": "user", "content": message.content})
#         reply = chat_with_character(self.history)
#         self.history.append({"role": "assistant", "content": reply})
#         await message.channel.send(reply)


# ---------------------------------------------------------------------------
# Dry-run demo (no Discord token needed — run this file directly to test
# compression behaviour)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    PERSONA = (
        "You are Lumi, a cheerful fox-spirit who lives in a forest shrine. "
        "Speak warmly, use light humour, and occasionally mention the weather "
        "or the forest. Stay in character."
    )
    test_history: list[dict] = [
        {"role": "system", "content": PERSONA},
    ]
    # Simulate a short conversation
    test_history.append({"role": "user", "content": "（轻轻敲了敲鸟居的柱子）有人在吗？"})
    test_history.append({"role": "assistant", "content": "（从树后探出头，耳朵先露了出来）来了来了！今天风有点凉，进来说话。"})
    test_history.append({"role": "user", "content": "带了点豆皮寿司。（放下纸袋）"})
    test_history.append({"role": "assistant", "content": "（眼睛一亮，但努力装出淡定的样子）咳……放那边就行。要喝茶吗？"})
    test_history.append({"role": "user", "content": "好。最近森林里有什么新鲜事？"})

    compressed = compress(test_history, COMPRESSOR_CONFIG)
    before = sum(len(m["content"]) for m in test_history if isinstance(m["content"], str))
    after = sum(len(m["content"]) for m in compressed if isinstance(m["content"], str))
    print(f"Library mode dry-run: {before} → {after} chars "
          f"(saved {before - after}, {(before - after) / before * 100:.1f}%)")
    print("Run with DISCORD_TOKEN set to connect a real bot.")
