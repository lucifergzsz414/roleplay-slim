# roleplay-slim Showcase

A 500-turn roleplay conversation — start to finish — through roleplay-slim's
compression pipeline. No cherry-picking, no hand-tuning. Just the default
config and the example config side by side.

**The question this page answers:** does the character still sound like
themselves after compression?

## Setup

- **Persona:** Aria, a shy guitarist (380 tokens, 2 leading system messages)
- **Dialogue:** 10 rotating scenes × 50 cycles = 500 turns, Chinese with
  parenthetical stage directions
- **Footer:** `[FORMAT RULE] End your reply with a mood tag.` repeated every turn
- **Benchmark script:** `benchmark/run_benchmark.py`

Run it yourself:
```bash
python benchmark/run_benchmark.py
```

---

## Raw numbers

| Config | 50 turns | 200 turns | 500 turns |
|---|---|---|---|
| No compression | 3,966 | 15,036 | 37,176 |
| Default (keep=6, trim) | 3,366 (15.1%) | 12,591 (16.3%) | 31,041 (16.5%) |
| + strip stage directions | 2,355 (40.6%) | 8,085 (46.2%) | 19,545 (47.4%) |

---

## What happens at each stage

### Stage 1: The persona prefix — never touched

```
[system] You are Aria, a shy guitarist. Stay in character. ...（380 tokens）
[system] [Session context] Format rules apply.
```

These two messages are byte-identical on every request. roleplay-slim detects
them as the cache-stable prefix and **never runs a single transform on them**.
DeepSeek's prompt cache hits every time. No config needed.

### Stage 2: Recent turns — kept verbatim

The last `keep_recent_turns=6` turns (12 messages) pass through untouched.
If Aria just said something shy and quiet on turn 500, it arrives at the LLM
exactly as written.

### Stage 3: Older turns — compressed

Turns 1 through 494 get:
- **Whitespace normalized** — collapsed blank lines
- **Stage directions stripped** (if enabled) — "（停下拨弦的手）" becomes just the
  dialogue
- **Trimmed to first+last sentence** — keeps the gist, drops the middle
- **Footers deduped** — `[FORMAT RULE]` appears once instead of 500 times

### Stage 4: The rebuilt message array

```
[system] Persona prefix          ← 380 tokens, untouched
[system] Shared config           ← untouched
[user] Turn 1 (first sentence)   ← trimmed stub
[assistant] Turn 1 (first+last)  ← trimmed stub
...                              ← turns 2-494: stubs, directions gone
[user] Turn 499                  ← recent, verbatim
[assistant] Turn 499             ← recent, verbatim
[user] Turn 500                  ← recent, verbatim
[assistant] Turn 500             ← recent, verbatim
[system] [FORMAT RULE] ...       ← only once
```

---

## Does the character change?

When you strip stage directions from older turns, you're removing:

```
"（抬头看了一眼）还没呢，练完这段再说。你手里拿的什么？"
→ "还没呢，练完这段再说。你手里拿的什么？"
```

The actual dialogue stays. The blocking notes, action cues, and internal
monologue markers are removed — but only from turns that fall outside the
`keep_recent_turns` window. The most recent interactions (where body language
matters most) are kept in full.

For a character like Aria, whose personality lives in *what she says* more
than *how she moves*, this has no perceptible effect on tone or consistency.

For a character whose personality is heavily carried by physical description
("always fidgeting with her hair", "never makes eye contact"), you'd want a
larger `keep_recent_turns` value or to keep `strip_stage_directions` off.

---

## The bottom line

**Same character. Same conversation. ~47% less cost with the example config.**
The persona prefix — the part that defines *who* the character is — is
structurally guaranteed to never be altered by compression.

That's the difference between a generic token compressor and a dialogue-native
context layer.

---

## Try it on your own data

```bash
pip install "roleplay-slim[all]"
export UPSTREAM_API_KEY=sk-your-key
roleplay-slim-proxy --config examples/example_config.toml
```

Point your app at `http://127.0.0.1:8791/v1` and every request prints a
one-line compression summary. No code changes needed.
