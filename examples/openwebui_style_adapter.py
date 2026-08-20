"""OpenWebUI-style adapter: how to point any generic OpenAI-compatible chat
UI (OpenWebUI, LibreChat, chatbot-ui, and similar) at roleplay-slim with
zero code changes.

These tools are the simplest possible integration target: they already
speak the OpenAI Chat Completions protocol and only need a base_url swap
— there is no bot-specific message-array shape to reason about, unlike a
QQ bot or a Discord bot with its own persona/history conventions. This
file is deliberately mostly documentation, not a runnable bot, because
there is no bot-specific code to write for this case.

Proxy mode (recommended — no code changes at all):

  1. Start the proxy:
     export UPSTREAM_API_KEY=sk-...
     roleplay-slim-proxy --config examples/example_config.toml

  2. In OpenWebUI (or your tool of choice), point the "OpenAI API Base URL"
     setting at:
       http://127.0.0.1:8791/v1

  3. Every request is compressed automatically. Poll GET /stats for
     cumulative token savings. Nothing else changes — your existing model
     picker, system prompt field, and conversation history all keep
     working exactly as before.

If you're building your own minimal OpenWebUI-style tool from scratch
(a generic multi-conversation chat UI, not tied to any one framework),
the library approach below shows the same pattern in code: the compressor
doesn't care what UI sent the messages, only their role/content shape."""

import os

from openai import OpenAI

from roleplay_slim import CompressorConfig, compress

# ---------------------------------------------------------------------------
# Config — move these to a config file or env vars in production
# ---------------------------------------------------------------------------
UPSTREAM_API_KEY = os.getenv("UPSTREAM_API_KEY", "sk-your-key")
LLM_MODEL = "deepseek-chat"       # or any OpenAI-compatible model
LLM_BASE_URL = "https://api.deepseek.com/v1"

# A generic chat UI's default system prompt tends to be short and its
# history tends to be long, unstructured turns (no repeated footers, no
# stage directions) — keep_recent_turns and history_window_mode do most
# of the work here; the roleplay-specific strategies are usually unneeded.
COMPRESSOR_CONFIG = CompressorConfig(
    keep_recent_turns=4,
    history_window_mode="trim",
)

client = OpenAI(base_url=LLM_BASE_URL, api_key=UPSTREAM_API_KEY)


def chat(messages: list[dict]) -> str:
    """Compress history, then call the LLM — the same call any generic
    chat UI's backend makes, with one line added."""
    compressed = compress(messages, COMPRESSOR_CONFIG)
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=compressed,
        max_tokens=512,
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Dry-run demo (no LLM call needed — run this file directly to test
# compression behaviour on a generic, non-roleplay conversation shape)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    SYSTEM_PROMPT = "You are a helpful assistant."
    test_history: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    # A generic assistant conversation — long plain-text turns, no repeated
    # footers or stage directions, unlike the roleplay examples. More turns
    # than keep_recent_turns=4 so trimming actually has something to do —
    # a shorter demo would show 0% savings (everything still inside the
    # recent-turns window), which is correct but unconvincing as a demo.
    # (extractive trim only touches text with 3+ sentences — a 2-sentence
    # answer is returned unchanged, so the first few answers here are
    # written longer than the later ones specifically to demonstrate that.)
    qa_pairs = [
        ("Can you explain what a hash map is?",
         "A hash map (or hash table) is a data structure that maps keys to values. It uses a hash function to compute an index into an array of buckets. Most languages' built-in dict/map types are hash maps under the hood."),
        ("What happens when two keys hash to the same index?",
         "That's called a collision. Common resolutions are chaining, where each bucket holds a list of entries, or open addressing, where the implementation probes for the next free slot. Which one a language picks affects worst-case behavior under heavy collisions."),
        ("Which one does Python's dict use?",
         "Open addressing, specifically a variant with pseudo-random probing. This has been the case since CPython 3.6's compact dict redesign. The change also made dicts preserve insertion order as a side effect."),
        ("What's the average time complexity for lookups?",
         "O(1) on average, assuming a reasonable hash function and load factor. Worst case degrades to O(n) if many keys collide."),
        ("How does the load factor affect performance?",
         "A higher load factor means more collisions and slower lookups; most implementations resize (rehash into a bigger table) once it crosses a threshold, often around 0.66-0.75."),
        ("Does resizing happen automatically?",
         "Yes — CPython's dict resizes automatically when it gets too full, so you don't need to manage capacity yourself."),
    ]
    for q, a in qa_pairs:
        test_history.append({"role": "user", "content": q})
        test_history.append({"role": "assistant", "content": a})
    test_history.append({"role": "user", "content": "One more — is a Python set implemented the same way?"})

    compressed = compress(test_history, COMPRESSOR_CONFIG)
    before = sum(len(m["content"]) for m in test_history if isinstance(m["content"], str))
    after = sum(len(m["content"]) for m in compressed if isinstance(m["content"], str))
    print(f"Library mode dry-run: {before} → {after} chars "
          f"(saved {before - after}, {(before - after) / before * 100:.1f}%)")
    assert compressed[0]["content"] == SYSTEM_PROMPT, "Prefix was modified!"
    print("  [OK] system prompt preserved byte-for-byte")
    print("For zero-code-change integration, use proxy mode — see the module docstring.")
