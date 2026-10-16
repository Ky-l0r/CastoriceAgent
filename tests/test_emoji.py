"""表情包系统回归测试

覆盖 EmojiCatalog.match_by_mood 与 EmojiSession.should_append 的概率 / 冷却判定。
这两个函数决定「AI 什么时候发表情包」，行为一旦漂移会直接影响聊天体验，
因此在改造前先钉住语义。测试全程离线、不需要 API Key。
"""

from pathlib import Path

import pytest

import ai

FALLBACK_MOODS = {"欣赏", "害羞", "疑问", "耍帅", "喜欢", "惊讶"}


# ----------------------------------------------------------------------------
# 夹具与工具
# ----------------------------------------------------------------------------

def make_catalog(tmp_path, names, subdir=True, **kwargs):
    """在临时目录里造一批表情包图片并建立目录对象"""
    base = tmp_path / "Image"
    target = base / ai.EmojiCatalog.SUBDIR_NAME if subdir else base
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        (target / f"{name}.png").write_bytes(b"\x89PNG")
    return ai.EmojiCatalog(image_dir=base, **kwargs)


# ============================================================================
# EmojiCatalog.match_by_mood
# ============================================================================

def test_empty_text_returns_none(tmp_path):
    catalog = make_catalog(tmp_path, ["喜欢"])
    assert catalog.match_by_mood("") is None
    assert catalog.match_by_mood(None) is None


def test_blank_text_without_keyword_still_returns_sticker(tmp_path):
    # 只有真正为空串才返回 None；纯空白会走兜底分支
    catalog = make_catalog(tmp_path, ["喜欢"])
    assert catalog.match_by_mood("   ") == "喜欢"


def test_matches_by_keyword_score(tmp_path):
    catalog = make_catalog(
        tmp_path,
        ["开心", "难过"],
        keywords={"开心": ["开心", "高兴"], "难过": ["难过", "伤心"]},
    )
    assert catalog.match_by_mood("今天真开心，也很高兴") == "开心"


def test_picks_unique_highest_score(tmp_path):
    catalog = make_catalog(
        tmp_path,
        ["A", "B"],
        keywords={"A": ["高兴", "开心"], "B": ["高兴"]},
    )
    # A 命中 2 个关键词、B 命中 1 个，唯一最高分
    assert catalog.match_by_mood("又高兴又开心") == "A"


def test_ties_are_resolved_randomly_but_within_top_score(tmp_path, monkeypatch):
    catalog = make_catalog(
        tmp_path,
        ["A", "B", "C"],
        keywords={"A": ["高兴"], "B": ["高兴"], "C": ["难过"]},
    )
    picked = []

    def fake_choice(seq):
        picked.append(list(seq))
        return sorted(seq)[0]

    monkeypatch.setattr(ai.random, "choice", fake_choice)
    result = catalog.match_by_mood("我很高兴")
    assert result == "A"
    assert picked and sorted(picked[0]) == ["A", "B"]


def test_keywords_of_unavailable_stickers_are_ignored(tmp_path):
    # 「难过」没有图片，不应该被选中
    catalog = make_catalog(tmp_path, ["开心"], keywords={"开心": ["开心"]})
    result = catalog.match_by_mood("我很难过但也要开心")
    assert result == "开心"


def test_single_character_keywords_are_ignored(tmp_path):
    catalog = make_catalog(tmp_path, ["X"], keywords={"X": ["想"]})
    # 单字关键词长度 < 2，不参与打分，于是走兜底
    assert catalog.match_by_mood("想") == "X"  # 兜底时目录里只有 X


def test_single_character_keywords_do_not_beat_real_matches(tmp_path):
    catalog = make_catalog(
        tmp_path,
        ["X", "Y"],
        keywords={"X": ["想"], "Y": ["喜欢"]},
    )
    assert catalog.match_by_mood("想你，喜欢") == "Y"


def test_fallback_returns_a_sticker_from_soft_mood_list(tmp_path):
    catalog = make_catalog(tmp_path, ["喜欢", "害羞", "惊讶", "生气", "哭泣"])
    for _ in range(30):
        assert catalog.match_by_mood("今天天气不错呢") in FALLBACK_MOODS


def test_fallback_avoids_strong_emotions(tmp_path):
    catalog = make_catalog(tmp_path, ["生气", "哭泣", "喜欢"])
    for _ in range(30):
        assert catalog.match_by_mood("嗯，好的") == "喜欢"


def test_fallback_uses_all_names_when_none_of_preferred_exists(tmp_path):
    catalog = make_catalog(tmp_path, ["生气", "哭泣"], keywords={})
    for _ in range(20):
        assert catalog.match_by_mood("嗯，好的") in {"生气", "哭泣"}


def test_empty_catalog_returns_none(tmp_path):
    catalog = make_catalog(tmp_path, [])
    assert catalog.match_by_mood("我很开心") is None
    assert catalog.names == []
    assert not catalog


def test_missing_image_dir_returns_none(tmp_path):
    catalog = ai.EmojiCatalog(image_dir=tmp_path / "does-not-exist")
    assert catalog.names == []
    assert catalog.match_by_mood("喜欢") is None


def test_prefers_emoji_subdirectory(tmp_path):
    catalog = make_catalog(tmp_path, ["喜欢"])
    assert catalog.image_dir.name == ai.EmojiCatalog.SUBDIR_NAME
    assert catalog.names == ["喜欢"]


def test_skips_underscore_assets_and_unsupported_suffixes(tmp_path):
    catalog = make_catalog(tmp_path, ["喜欢"])
    (catalog.image_dir / "_icon.png").write_bytes(b"x")
    (catalog.image_dir / "note.txt").write_text("x", encoding="utf-8")
    catalog.reload()
    assert catalog.names == ["喜欢"]


def test_avatar_file_is_not_treated_as_sticker(tmp_path):
    catalog = make_catalog(tmp_path, ["喜欢", "CastoriceAvatar"])
    assert catalog.names == ["喜欢"]


def test_normalize_variants(tmp_path):
    catalog = make_catalog(tmp_path, ["喜欢", "害羞"])
    assert catalog._normalize("喜欢表情包") == "喜欢"
    assert catalog._normalize("  '害羞'  ") == "害羞"
    assert catalog._normalize("贴纸喜欢") == "喜欢"
    assert "喜欢表情包" in catalog


def test_duplicate_names_share_one_label(tmp_path):
    catalog = make_catalog(tmp_path, ["喜欢"])
    (catalog.image_dir / "喜欢.jpg").write_bytes(b"x")
    catalog.reload()
    assert catalog.names == ["喜欢"]
    assert catalog.count == 1
    assert catalog.get("喜欢") is not None


# ============================================================================
# EmojiSession.should_append —— 概率
# ============================================================================

def test_disabled_never_appends():
    session = ai.EmojiSession(enabled=False, probability=1.0)
    session.mark_turn()
    assert session.should_append() is False


def test_probability_one_always_appends():
    session = ai.EmojiSession(probability=1.0)
    for _ in range(5):
        session.mark_turn()
        assert session.should_append() is True


def test_probability_one_ignores_cooldown():
    session = ai.EmojiSession(probability=1.0, cooldown_turns=5)
    session.mark_turn()
    session.mark_sent()
    session.mark_turn()
    assert session.should_append() is True


def test_probability_zero_never_appends_after_first_turn(monkeypatch):
    monkeypatch.setattr(ai.random, "random", lambda: 0.0)
    session = ai.EmojiSession(probability=0.0, cooldown_turns=0)
    session.mark_turn()
    session.mark_sent()
    for _ in range(3):
        session.mark_turn()
        assert session.should_append() is False


def test_first_turn_always_appends(monkeypatch):
    """新会话第一轮必发一次，让对话立刻有情绪"""
    monkeypatch.setattr(ai.random, "random", lambda: 0.999)
    session = ai.EmojiSession(probability=0.1, cooldown_turns=2)
    session.mark_turn()
    assert session.should_append() is True


def test_random_roll_below_probability_appends(monkeypatch):
    monkeypatch.setattr(ai.random, "random", lambda: 0.10)
    session = ai.EmojiSession(probability=0.5, cooldown_turns=0)
    session.mark_turn()
    session.mark_sent()
    session.mark_turn()
    assert session.should_append() is True


def test_random_roll_above_probability_skips(monkeypatch):
    monkeypatch.setattr(ai.random, "random", lambda: 0.90)
    session = ai.EmojiSession(probability=0.5, cooldown_turns=0)
    session.mark_turn()
    session.mark_sent()
    session.mark_turn()
    assert session.should_append() is False


def test_probability_is_clamped_to_unit_interval():
    assert ai.EmojiSession(probability=5.0).probability == 1.0
    assert ai.EmojiSession(probability=-3.0).probability == 0.0
    assert ai.EmojiSession(probability=0.35).probability == 0.35


def test_probability_from_string_is_rejected_safely():
    # 非数字会抛 TypeError，这里只钉住当前行为，避免以后悄悄改变
    with pytest.raises(TypeError):
        ai.EmojiSession(probability="0.5")


# ============================================================================
# EmojiSession.should_append —— 冷却
# ============================================================================

def test_cooldown_blocks_turns_inside_window(monkeypatch):
    monkeypatch.setattr(ai.random, "random", lambda: 0.0)
    session = ai.EmojiSession(probability=0.5, cooldown_turns=2)

    session.mark_turn()                      # turn 1
    assert session.should_append() is True   # 第一轮必发
    session.mark_sent()

    session.mark_turn()                      # turn 2
    assert session.should_append() is False
    session.mark_turn()                      # turn 3
    assert session.should_append() is False


def test_cooldown_expires_after_window(monkeypatch):
    monkeypatch.setattr(ai.random, "random", lambda: 0.0)
    session = ai.EmojiSession(probability=0.5, cooldown_turns=2)
    session.mark_turn()
    session.mark_sent()
    for _ in range(2):
        session.mark_turn()
        session.should_append()
    session.mark_turn()                      # turn 4：距上次发送隔了 3 轮
    assert session.should_append() is True


def test_cooldown_zero_allows_next_turn(monkeypatch):
    monkeypatch.setattr(ai.random, "random", lambda: 0.0)
    session = ai.EmojiSession(probability=0.5, cooldown_turns=0)
    session.mark_turn()
    session.mark_sent()
    session.mark_turn()
    assert session.should_append() is True


def test_negative_cooldown_is_clamped_to_zero():
    session = ai.EmojiSession(cooldown_turns=-5)
    assert session.cooldown_turns == 0


def test_cooldown_only_counts_from_actual_send(monkeypatch):
    """没真正发送（只做判定）不会触发冷却"""
    monkeypatch.setattr(ai.random, "random", lambda: 0.0)
    session = ai.EmojiSession(probability=0.5, cooldown_turns=2)
    session.mark_turn()
    session.should_append()
    session.mark_turn()
    assert session.should_append() is True


# ============================================================================
# EmojiSession —— 会话上限与状态
# ============================================================================

def test_max_per_session_blocks_further_sends():
    session = ai.EmojiSession(probability=1.0, max_per_session=1)
    session.mark_turn()
    assert session.should_append() is True
    session.mark_sent()
    session.mark_turn()
    assert session.should_append() is False
    assert session.sent_count == 1


def test_max_per_session_zero_means_unlimited():
    session = ai.EmojiSession(probability=1.0, max_per_session=0)
    for _ in range(10):
        session.mark_turn()
        assert session.should_append() is True
        session.mark_sent()
    assert session.sent_count == 10


def test_negative_max_per_session_is_clamped_to_zero():
    assert ai.EmojiSession(max_per_session=-1).max_per_session == 0


def test_should_append_has_no_side_effects(monkeypatch):
    monkeypatch.setattr(ai.random, "random", lambda: 0.0)
    session = ai.EmojiSession(probability=0.5, cooldown_turns=1)
    session.mark_turn()
    session.mark_sent()
    session.mark_turn()
    results = [session.should_append() for _ in range(10)]
    assert set(results) == {False}
    assert session.turn == 2
    assert session.sent_count == 1


def test_counters_and_reset():
    session = ai.EmojiSession(probability=1.0)
    session.mark_turn()
    session.mark_sent()
    session.mark_turn()
    assert (session.turn, session.sent_count) == (2, 1)

    session.reset()
    assert (session.turn, session.sent_count) == (0, 0)
    # reset 之后回到「新会话」语义：第一轮必发
    session.mark_turn()
    assert session.should_append() is True


def test_defaults_match_chat_experience():
    """默认值即产品语义：约三分之一概率、发过一次隔两轮"""
    session = ai.EmojiSession()
    assert session.enabled is True
    assert session.probability == pytest.approx(0.35)
    assert session.cooldown_turns == 2
    assert session.max_per_session == 0


def test_before_first_turn_follows_probability(monkeypatch):
    """回合还没开始时也不走「第一轮必发」分支"""
    monkeypatch.setattr(ai.random, "random", lambda: 0.99)
    assert ai.EmojiSession().should_append() is False
    monkeypatch.setattr(ai.random, "random", lambda: 0.0)
    assert ai.EmojiSession().should_append() is True
