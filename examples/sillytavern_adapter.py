"""SillyTavern adapter: wire roleplay-slim into a SillyTavern-style chat
pipeline. Demonstrates three things that matter for ST users:

  1. Character card (long system persona) is auto-detected as the cache-safe
     prefix and left byte-for-byte untouched — hits DeepSeek/OpenAI cache.
  2. Asterisk-style stage directions (*like this*) from older turns are
     stripped — keeping dialogue, dropping action cues.
  3. Library mode integrates at the point where your app assembles the
     messages array, right before the LLM call.

SillyTavern's message structure (simplified):
  [system]  Character card (persona, scenario, example dialogue) — STATIC
  [system]  Jailbreak / formatting rules — STATIC
  [user]    Turn 1
  [character] Turn 1 reply
  ...

roleplay-slim's default heuristic sees the leading system messages and
treats them as the cache-stable prefix — no config needed for that part.

Run this file directly for a dry-run demo (no API key required):
    python examples/sillytavern_adapter.py
"""

from roleplay_slim import CompressorConfig, compress

# ---------------------------------------------------------------------------
# SillyTavern-style character card (simplified)
# ---------------------------------------------------------------------------
CHARACTER_CARD = """[Character: Elara Nightshade]
Age: appears 20s (actually 300+). Race: half-fae, half-human.
Role: keeper of the Moonlit Library, a place that exists between worlds.

Personality: Curious but guarded. Speaks in a warm, slightly archaic tone —
thinks in paragraphs, speaks in sentences. Uses the occasional "thee" and
"thou" when flustered. Has a dry, understated sense of humor that takes
people three exchanges to notice.

Likes: forgotten languages, the smell of old paper, moonlight on marble,
tea that's been steeping too long.

Dislikes: loud questions, people who dog-ear pages, the color orange
(reminds her of the fire that took the original library).

Scenario: {{user}} has stumbled into the Moonlit Library while searching
for a rare grimoire. Elara is the first person they've seen in days.

Example dialogue:
{{user}}: Who are you?
{{char}}: *She lifts her gaze from a tome the size of a dinner plate, one
eyebrow arching.* I am the one who decides whether you leave with that book
— or leave at all. *The threat hangs in the air a beat too long before she
smiles.* Relax. I'm Elara. Tea? It's been steeping too long, which means
it's perfect. """

JAILBREAK = "[Instructions: Stay in character. Use *asterisks* for actions.]"

# ---------------------------------------------------------------------------
# Compression config (asterisk stage directions — SillyTavern's convention)
# ---------------------------------------------------------------------------
CONFIG = CompressorConfig(
    keep_recent_turns=3,
    enable_strip_stage_directions=True,
    stage_direction_pattern="asterisk",     # *action cues*
    enable_dedupe_verbatim_tail=True,
    enable_whitespace_normalize=True,
)

# ---------------------------------------------------------------------------
# Build a simulated 8-turn SillyTavern conversation
# ---------------------------------------------------------------------------
def build_messages() -> list[dict]:
    """Assemble a SillyTavern-style message array."""
    turns = [
        # (user, character)
        (
            "*Peering through the endless shelves* Hello? Is anyone here?",
            "*She appears from behind a shelf without a sound, book in hand.* "
            "There is. The question is whether you should be. *She studies your "
            "face for a moment.* You're not a thief. Thieves don't say hello.",
        ),
        (
            "I'm looking for the Codex of Vanished Moons. A scholar in "
            "Valdris said it might be here.",
            "*Her eyes narrow — not in suspicion, but interest.* Valdris. "
            "Haven't heard that name in sixty years. The Codex is in the "
            "restricted wing. *She tilts her head.* What does a mortal want "
            "with a book on dead moons?",
        ),
        (
            "It's not for me. It's for someone who's running out of time.",
            "*The playfulness leaves her face.* Those are the only requests "
            "that get past the door. *She sets her book down.* Follow me. And "
            "don't touch anything with a red spine — they bite.",
        ),
        (
            "*Following her down a corridor that seems to get longer with "
            "every step* Does the library ever... change while you're inside?",
            "*She glances back, and for a split second she looks every one of "
            "her three hundred years.* Constantly. Last Tuesday I went looking "
            "for the poetry section and found a glacier. *A beat.* The library "
            "has a sense of humor. I don't.",
        ),
    ]
    msgs: list[dict] = [
        {"role": "system", "content": CHARACTER_CARD},
        {"role": "system", "content": JAILBREAK},
    ]
    for user_msg, char_msg in turns:
        msgs.append({"role": "user", "content": user_msg})
        msgs.append({"role": "assistant", "content": char_msg})
    # Simulate a per-turn footer that repeats (like SillyTavern's author's note)
    msgs.append({"role": "system", "content": "[AN: Elara is in her library.]"})
    return msgs


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    messages = build_messages()
    compressed = compress(messages, CONFIG)

    before_chars = sum(
        len(m["content"]) for m in messages if isinstance(m["content"], str)
    )
    after_chars = sum(
        len(m["content"]) for m in compressed if isinstance(m["content"], str)
    )

    print("=== SillyTavern Adapter — Compression Dry-Run ===\n")
    print(f"Messages before: {len(messages)}")
    print(f"Messages after:  {len(compressed)}")
    print(f"Chars: {before_chars} -> {after_chars} "
          f"(saved {before_chars - after_chars}, "
          f"{(before_chars - after_chars) / before_chars * 100:.1f}%)\n")

    # Verify character card untouched
    assert compressed[0]["content"] == CHARACTER_CARD, (
        "CHARACTER CARD WAS MODIFIED — this is a bug"
    )
    assert compressed[1]["content"] == JAILBREAK, (
        "JAILBREAK WAS MODIFIED — this is a bug"
    )
    print("[OK] Character card + jailbreak preserved byte-for-byte")
    print("[OK] Ready to wire into your SillyTavern pipeline.")
