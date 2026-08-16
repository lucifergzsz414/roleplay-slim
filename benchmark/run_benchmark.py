"""Benchmark: measure token savings across different compressor configs
on synthetic roleplay conversations at 50, 200, and 500 turns.

Generates numbers suitable for a README table — no real user data,
no external dependencies beyond roleplay-slim itself."""

from roleplay_slim import CompressorConfig, compress
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
    scenarios = [50, 200, 500]
    rows: list[tuple[int, int, int, int]] = []

    for turns in scenarios:
        messages = build_conversation(turns)
        msg_count = len(messages)
        print(f"roleplay-slim benchmark — {turns}-turn conversation "
              f"({msg_count} messages, 2-message cache-stable prefix)")

        default_cfg = CompressorConfig()
        _, after_d_tok, _, _ = bench("  default (keep=6, trim)",
                                     default_cfg, messages)

        example_cfg = CompressorConfig(
            keep_recent_turns=6,
            enable_strip_stage_directions=True,
            stage_direction_pattern="fullwidth_parens",
        )
        _, after_e_tok, _, _ = bench("  example_config (keep=6, trim+strip)",
                                     example_cfg, messages)

        before_tok = estimate_messages_tokens(messages)
        rows.append((turns, before_tok, after_d_tok, after_e_tok))
        print()

    # Summary table
    print("=" * 72)
    print(f"{'Turns':>6}  {'Before':>7}  {'Default':>7}  {'Default %':>9}  "
          f"{'Example':>7}  {'Example %':>9}")
    print("-" * 72)
    for turns, before, after_d, after_e in rows:
        dpct = (before - after_d) / before * 100
        epct = (before - after_e) / before * 100
        print(f"{turns:>6}  {before:>7}  {after_d:>7}  {dpct:>8.1f}%  "
              f"{after_e:>7}  {epct:>8.1f}%")
    print("=" * 72)


if __name__ == "__main__":
    main()
