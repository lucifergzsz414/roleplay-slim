"""Minimal library-usage example: import roleplay_slim, compress a few
messages, and print the result.

Run with any OpenAI-compatible client by swapping base_url — no code
changes needed beyond the HTTP layer."""

from roleplay_slim import CompressorConfig, compress

messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"},
    {"role": "assistant", "content": "Hi there! How can I help?"},
    {"role": "user", "content": "Tell me about prompt caching."},
]

config = CompressorConfig(keep_recent_turns=3)
compressed = compress(messages, config)

print(f"Before: {len(messages)} messages")
print(f"After:  {len(compressed)} messages")
