from datetime import UTC, datetime, timedelta

import pytest

from pigeon.repo.store import QuestDeck, Store

NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)
END = NOW + timedelta(days=7)


def test_quests_decks_titles_and_transaction_rollback(tmp_path):
    path = tmp_path / "db"
    store = Store(path)
    for chat in (1, 2):
        store.enable_chat(chat, NOW, "UTC")
        store.create_quest(chat, "2026-11-02", "trio-hit")
        store.save_quest_deck(chat, QuestDeck(["team-scare"], "trio-hit"))
    with pytest.raises(RuntimeError), store.transaction():
        store.fail_previous_quests(1, "2026-11-09")
        store.save_quest_deck(1, QuestDeck([], "team-scare"))
        store.create_quest(1, "2026-11-09", "team-scare")
        raise RuntimeError("abort")
    assert store.get_quest(1, "2026-11-02").status == "active"
    assert store.get_quest(1, "2026-11-09") is None
    assert store.get_quest_deck(1) == QuestDeck(["team-scare"], "trio-hit")
    with pytest.raises(RuntimeError), store.transaction():
        assert store.complete_quest(1, "2026-11-02", 1, "title", NOW)
        raise RuntimeError("abort")
    assert store.get_quest(1, "2026-11-02").status == "active"
    assert store.latest_chat_title(1) is None
    assert store.complete_quest(1, "2026-11-02", 1, "title", NOW)
    assert not store.complete_quest(1, "2026-11-02", 2, "other", NOW)
    assert store.get_quest(1, "2026-11-02").completed_event_id == 1
    assert store.latest_chat_title(1) == "title"
    assert store.latest_chat_title(2) is None
    store.fail_previous_quests(2, "2026-11-09")
    assert store.get_quest(2, "2026-11-02").status == "failed"
    store.close()
    store = Store(path)
    assert store.latest_chat_title(1) == "title"
    assert store.get_quest_deck(2) == QuestDeck(["team-scare"], "trio-hit")
    store.close()


def test_progress_queries_scope_time_chat_success_and_order(tmp_path):
    store = Store(tmp_path / "db")
    for chat in (1, 2):
        store.enable_chat(chat, NOW, "UTC")
    event = store.open_event(1, NOW, END, "manual", "A", "A")
    other = store.open_event(2, NOW, END, "manual", "B", "B")
    store.add_hit(1, event, 1, NOW, 1)
    store.add_hit(1, event, 2, NOW, 0)
    store.add_shoo(1, event, 1, "a", "b", False, NOW)
    store.add_hit(1, event, 3, NOW, 2)
    store.add_shoo(1, event, 2, "a", "b", True, NOW)
    store.add_hit(1, event, 4, NOW, 1)  # after the successful shoo
    store.add_hit(1, event, 5, END, 1)  # end is exclusive
    store.add_hit(2, other, 1, NOW, 1)
    assert store.successful_hits_in_period(1, NOW, END) == 3
    assert store.max_hit_participants(1, NOW, END) == 3
    assert store.max_shoo_participants(1, NOW, END) == 2
    assert store.max_hits_before_flee(1, NOW, END) == 2
    assert store.successful_hits_in_period(2, NOW, END) == 1
    assert store.max_shoo_participants(2, NOW, END) == 0
    assert store.max_hits_before_flee(2, NOW, END) == 0
    assert store.successful_hits_in_period(1, END, END + timedelta(days=1)) == 1
    store.close()
