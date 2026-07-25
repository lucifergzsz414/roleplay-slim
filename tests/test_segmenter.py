from roleplay_slim.segmenter import detect_prefix_length, segment, split_into_turns


def test_detect_prefix_length_leading_system_only():
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "system", "content": "shared block"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert detect_prefix_length(messages) == 2


def test_detect_prefix_length_no_leading_system():
    messages = [{"role": "user", "content": "hi"}]
    assert detect_prefix_length(messages) == 0


def test_detect_prefix_length_all_system():
    messages = [{"role": "system", "content": "a"}, {"role": "system", "content": "b"}]
    assert detect_prefix_length(messages) == 2


def test_detect_prefix_length_override():
    messages = [
        {"role": "system", "content": "a"},
        {"role": "system", "content": "b"},
        {"role": "user", "content": "hi"},
    ]
    assert detect_prefix_length(messages, override=1) == 1


def test_split_into_turns_groups_by_user_message():
    dynamic = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "system", "content": "footer1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    turns = split_into_turns(dynamic)
    assert len(turns) == 2
    assert [m["role"] for m in turns[0].messages] == ["user", "assistant", "system"]
    assert [m["role"] for m in turns[1].messages] == ["user", "assistant"]


def test_segment_returns_prefix_unmodified_and_turns():
    messages = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    prefix, turns = segment(messages)
    assert prefix == [{"role": "system", "content": "persona"}]
    assert len(turns) == 2
