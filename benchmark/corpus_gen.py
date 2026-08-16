"""Synthetic roleplay-conversation generator with planted, verifiable facts.

run_benchmark.py measures token savings. This is the other half of the
evidence story: **fidelity**. A compressor that quietly drops whole old turns
looks great on a savings table but loses the persona's memory of the
conversation — the exact failure mode a companion/roleplay app cares most
about. So every generated conversation plants a set of facts (distinctive
brand+object pairs) inside the *dynamic* region, and the fidelity runner
checks whether each fact's distinctive substring still survives compression.

Everything here is synthetic and deterministic: fixed wordlists + a seeded
RNG, so the same ``seed`` yields byte-identical output. That makes the
benchmark reproducible and lets any future compression-strategy change that
shifts retention surface as a test failure instead of a drift story. No real
user data ever enters the corpus.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from roleplay_slim.segmenter import detect_prefix_length

# Two-message cache-stable prefix, mirroring the shape a real companion bot
# sends (persona + shared memory block). Kept identical in structure to
# run_benchmark.py so the two benchmarks compose on the same message shape.
PERSONA = "You are Aria, a shy guitarist. Stay in character. " * 20
SHARED = "[Session context — not part of the roleplay]\nFormat rules apply."
FOOTER = "[FORMAT RULE] End your reply with a mood tag."

# Scene pairs for ordinary (non-fact) turns. A fact turn replaces the user
# line with the planted-fact sentence and keeps the same assistant reply, so
# the conversation still reads like dialogue rather than a fact dump.
SCENES = [
    ("（推开练习室的门）今天来得好早啊，吃过饭了吗？",
     "（抬头看了一眼）还没呢，练完这段再说。你手里拿的什么？"),
    ("（晃了晃手里的纸袋）楼下那家新开的可丽饼，排了二十分钟。",
     "（停下拨弦的手）……给我的？"),
    ("（把纸袋放在桌上）不然呢，我自己又吃不完两个。趁热。",
     "（小声）谢谢。那个……今天的和弦进行我改了改，你听听看。"),
    ("（低头看着指板）我知道，每次到那就紧张。再来一次，你帮我数拍子。",
     "（搬了把椅子坐到她旁边，手指在膝盖上轻轻敲节奏）一、二、三——"),
]

# Distinctive planted-fact material. Brand + object are drawn independently
# via the seeded RNG, so each fact's substring (e.g. "莱茵牌电吉他") is
# unique enough that "did it survive" is a reliable check — no accidental
# matches in the ordinary dialogue word pool.
BRANDS = ["莱茵", "青岚", "雾都", "桔梗", "弦月", "鹤见", "夜风", "苍梧"]
OBJECTS = ["电吉他", "节拍器", "护弦油", "效果器", "变调夹", "琴盒", "拨片", "调音器"]
FACT_NOTES = ["新买的", "攒了半年钱才买的", "从二手市场淘来的", "上周刚订的"]


@dataclass
class Fact:
    """One planted fact. ``feature`` is the distinctive substring the
    fidelity runner searches the compressed output for; ``subject`` /
    ``detail`` carry the pieces for a future LLM-based (Tier B) check."""

    id: str
    subject: str
    detail: str
    feature: str
    turn_index: int
    user_msg: str


@dataclass
class Sample:
    id: str
    messages: list[dict]
    facts: list[Fact]
    prefix_len: int = field(init=False)

    def __post_init__(self) -> None:
        self.prefix_len = detect_prefix_length(self.messages)


def _plant_fact(
    rng: random.Random, fact_no: int, turn_index: int, used_features: set[str]
) -> tuple[Fact, str]:
    # Redraw until the feature is unique across the sample. The retention
    # check is substring-based: if two facts shared a feature, one message
    # surviving would report *both* as retained, silently lying about the
    # other's loss. Rejection sampling keeps it deterministic (seeded RNG).
    while True:
        brand = rng.choice(BRANDS)
        obj = rng.choice(OBJECTS)
        note = rng.choice(FACT_NOTES)
        # Feature includes the 的 particle exactly as the message spells it —
        # the substring check is byte-exact, so the feature must be a literal
        # substring of the planted sentence.
        feature = f"{brand}牌的{obj}"
        if feature not in used_features:
            used_features.add(feature)
            break
    # Single sentence on purpose: extractive trim keeps the first and last
    # sentence, so a one-line fact statement survives trim mode — which is
    # the realistic "user said a thing" case, distinct from being dropped.
    user_msg = f"（随口聊起）对了，{note}，{feature}。"
    fact = Fact(
        id=f"f{fact_no}",
        subject=obj,
        detail=feature,
        feature=feature,
        turn_index=turn_index,
        user_msg=user_msg,
    )
    return fact, user_msg


def generate(
    seed: int,
    turns: int = 50,
    fact_interval: int = 5,
    *,
    id_prefix: str = "rp",
) -> Sample:
    """Generate a deterministic roleplay conversation with planted facts.

    A fact is planted in every user turn whose index is a multiple of
    ``fact_interval``, and always in the final turn (so the "recent" bucket
    is never empty regardless of ``turns`` — the fidelity tests rely on
    being able to distinguish retained vs. dropped facts by turn age).
    """
    rng = random.Random(seed)
    messages: list[dict] = [
        {"role": "system", "content": PERSONA},
        {"role": "system", "content": SHARED},
    ]
    facts: list[Fact] = []
    fact_no = 0
    used_features: set[str] = set()

    # Always plant a fact in the last turn as well, so there is at least one
    # fact inside any non-empty recent window.
    fact_turns = {t for t in range(turns) if t % fact_interval == 0}
    fact_turns.add(turns - 1)

    for t in range(turns):
        if t in fact_turns:
            fact, user_text = _plant_fact(rng, fact_no, t, used_features)
            facts.append(fact)
            fact_no += 1
        else:
            user_text = SCENES[t % len(SCENES)][0]
        assistant_text = SCENES[t % len(SCENES)][1]
        messages.append({"role": "user", "content": user_text})
        messages.append({"role": "assistant", "content": assistant_text})
        messages.append({"role": "system", "content": FOOTER})

    return Sample(id=f"{id_prefix}-{seed}", messages=messages, facts=facts)


def generate_corpus(
    seeds: list[int],
    turns: int = 50,
    fact_interval: int = 5,
) -> list[Sample]:
    return [generate(seed, turns, fact_interval) for seed in seeds]
