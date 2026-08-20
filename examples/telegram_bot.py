"""Telegram bot example: wire roleplay-slim into a python-telegram-bot
message pipeline. Demonstrates the library approach — import and call
compress() before sending to the LLM.

Setup:
  1. pip install python-telegram-bot openai "roleplay-slim[all]"
  2. Set TELEGRAM_BOT_TOKEN and UPSTREAM_API_KEY environment variables.
  3. python telegram_bot.py

This is a minimal skeleton — add your own persona, per-chat history
storage, and error handling for production use. Telegram is a natural fit
for the per-turn structure this library assumes: each chat_id gets its own
message array, growing one user/assistant pair per exchange, exactly the
shape compress() expects."""

import os

from openai import OpenAI

from roleplay_slim import CompressorConfig, compress

# ---------------------------------------------------------------------------
# Config — move these to a config file or env vars in production
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
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
# Telegram bot (minimal — no polling/webhook boilerplate here, import this
# module's chat_with_character() from your real bot.py)
# ---------------------------------------------------------------------------
# from telegram import Update
# from telegram.ext import Application, MessageHandler, ContextTypes, filters
#
# PERSONA = "You are Lumi, a cheerful fox-spirit..."
# histories: dict[int, list[dict]] = {}  # chat_id -> messages
#
# async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     chat_id = update.effective_chat.id
#     history = histories.setdefault(chat_id, [{"role": "system", "content": PERSONA}])
#     history.append({"role": "user", "content": update.message.text})
#     reply = chat_with_character(history)
#     history.append({"role": "assistant", "content": reply})
#     await update.message.reply_text(reply)
#
# if __name__ == "__main__":
#     app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
#     app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
#     app.run_polling()


# ---------------------------------------------------------------------------
# Dry-run demo (no Telegram token needed — run this file directly to test
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
    # Simulate a short conversation — one chat_id's worth of history, the
    # exact shape a real per-chat dict entry would hold.
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
    assert compressed[0]["content"] == PERSONA, "Prefix was modified!"
    print("  [OK] persona prefix preserved byte-for-byte")
    print("Run with TELEGRAM_BOT_TOKEN set to connect a real bot.")
