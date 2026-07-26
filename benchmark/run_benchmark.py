"""Benchmark: measure token savings across different compressor configs
on a synthetic 50-turn roleplay conversation.

Generates numbers suitable for a README table — no real user data,
no external dependencies beyond roleplay-slim itself."""

from roleplay_slim import compress, CompressorConfig
from roleplay_slim.stats import estimate_messages_tokens

PERSONA = "You are Aria, a shy guitarist. Stay in character. " * 20
SHARED = "[Session context — not part of the roleplay]\nFormat rules apply."
FOOTER = "[FORMAT RULE] End your reply with a mood tag."

# A few distinct topic mini-arcs so the conversation feels less mechanical
# than repeating the same two lines 50 times.
SCENES = [
    ("（推开练习室的门）今天来得好早啊，吃过饭了吗？",
     "（抬头看了一眼）还没呢，练完这段再说。你手里拿的什么？"),
    ("（晃了晃手里的纸袋）楼下那家新开的可丽饼，排了二十分钟。",
     "（停下拨弦的手）……给我的？"),
    ("（把纸袋放在桌上）不然呢，我自己又吃不完两个。趁热。",
     "（小声）谢谢。那个……今天的和弦进行我改了改，你听听看。"),
    ("她弹了一段比昨天流畅很多的琶音，虽然最后两个音还是有点赶。",
     "（等她弹完才敢呼吸）最后那里再慢半拍就完美了。"),
    ("（低头看着指板）我知道，每次到那就紧张。再来一次，你帮我数拍子。",
     "（搬了把椅子坐到她旁边，手指在膝盖上轻轻敲节奏）一、二、三——"),
    ("这次的结尾稳了很多，她自己显然也满意，嘴角往上翘了一点点。",
     "（转过头看你）怎么样。"),
    ("（竖起大拇指）进步巨大。再来一遍就能录了。",
     "（摇摇头）今天不练了。手腕有点酸。"),
    ("（条件反射地去拿她的手腕看了看）我看看……还好没肿。休息吧。",
     "（没抽手，只是把脸转向另一侧）你上次说的那个比赛，报名了吗。"),
    ("（愣了一下）你还记得那个。我没报，觉得曲目准备不够。",
     "（转回来看着你）你比我弹得好多了，为什么不去。"),
    ("（被她说得有点不好意思）行，那你去给我当拉拉队我就报。",
     "（轻轻笑了一声）……看情况。"),
]

LONG_TAIL = [
    ("（翻着乐谱）接下来练哪首？",
     "（指着第三页）这首。上次副歌的切分音我一直踩不准。"),
    ("那个切分确实难，要不先把节奏型单独抽出来练？",
     "（点头）好。你帮我打拍子。不要越打越快，上次就是。"),
    ("（举手投降）上次那是激动了嘛。这次保证稳。",
     "（嘴角动了动）信你一回。"),
    ("练了大概四十分钟，中间停下来讨论了两次指法。切分终于稳了。",
     "（用袖子擦了擦额头的汗）差不多到极限了。"),
    ("辛苦了。收拾一下我请你喝东西，楼下奶茶店？",
     "（站起来活动肩膀）……红豆烤奶，去冰。"),
]


def build_conversation(num_turns: int = 50) -> list[dict]:
    """Build a synthetic roleplay conversation with a realistic message
    structure: persona prefix, shared-context block, then alternating
    user/assistant turns with a repeated footer system message each turn."""
    messages: list[dict] = [
        {"role": "system", "content": PERSONA},
        {"role": "system", "content": SHARED},
    ]
    for i in range(num_turns):
        scene = SCENES[i % len(SCENES)]
        messages.append({"role": "user", "content": scene[0]})
        messages.append({"role": "assistant", "content": scene[1]})
        messages.append({"role": "system", "content": FOOTER})

    # Append a few more turns from the long tail to push past 50
    for i, (user_line, asst_line) in enumerate(LONG_TAIL):
        messages.append({"role": "user", "content": user_line})
        messages.append({"role": "assistant", "content": asst_line})
        messages.append({"role": "system", "content": FOOTER})

    return messages


def bench(label: str, config: CompressorConfig, messages: list[dict]):
    before = estimate_messages_tokens(messages)
    compressed = compress(messages, config)
    after = estimate_messages_tokens(compressed)
    saved = before - after
    pct = (saved / before * 100) if before else 0.0
    msg_count = len(compressed)
    print(f"  {label:40s} {before:>6d} → {after:>6d} tokens  "
          f"saved {saved:>6d} ({pct:5.1f}%)  {msg_count:>3d} messages")
    return before, after, saved, pct


def main():
    messages = build_conversation(50)
    total_turns = (len(messages) - 2) // 3  # minus prefix, each turn = 3 msgs

    print(f"roleplay-slim benchmark — {total_turns}-turn conversation "
          f"({len(messages)} messages total, with 2-message cache-stable prefix)\n")

    # Default config (keep_recent_turns=6, trim mode, no stage-direction stripping)
    bench("default (keep=6, trim)", CompressorConfig(), messages)

    # Config matching example_config.toml (stage directions enabled)
    bench("example_config.toml (keep=6, trim + strip)",
          CompressorConfig(
              keep_recent_turns=6,
              enable_strip_stage_directions=True,
              stage_direction_pattern="fullwidth_parens",
          ),
          messages)

    # Aggressive: fewer recent turns, stage-direction stripping on
    bench("aggressive (keep=3, trim + strip)",
          CompressorConfig(
              keep_recent_turns=3,
              enable_strip_stage_directions=True,
          ),
          messages)

    # Maximum compression: drop old turns entirely, strip directions
    bench("max (keep=3, drop + strip)",
          CompressorConfig(
              keep_recent_turns=3,
              history_window_mode="drop",
              enable_strip_stage_directions=True,
          ),
          messages)


if __name__ == "__main__":
    main()
