from datetime import UTC, datetime, timedelta

import pytest

from pigeon.clock import FixedClock
from pigeon.render import texts
from pigeon.repo.store import Store
from pigeon.scheduler import duel_sweep
from pigeon.services import achievements, duels

CHAT = -9001
OTHER = -9002
A, B, C = 1, 2, 3
NOON = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


class FakeEditor:
    def __init__(self, fail: bool = False) -> None:
        self.edits: list[tuple[int, int, str]] = []
        self.fail = fail

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> None:
        if self.fail:
            raise RuntimeError("message is gone")
        self.edits.append((chat_id, message_id, text))


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "pigeon.db")
    s.enable_chat(CHAT, NOON, "Europe/Moscow")
    s.enable_chat(OTHER, NOON, "Europe/Moscow")
    s.upsert_member(CHAT, A, "Anna")
    s.upsert_member(CHAT, B, "Boris")
    yield s
    s.close()


@pytest.fixture
def clock():
    return FixedClock(NOON)


def _accepted(store, clock):
    duel_id = duels.challenge(store, clock, CHAT, A, B)
    assert duels.accept(store, clock, CHAT, duel_id, B) is duels.Answer.OK
    return store.get_duel(CHAT, duel_id)


def test_one_live_duel_per_chat(store, clock) -> None:
    assert duels.challenge(store, clock, CHAT, A, B) is not None
    assert duels.challenge(store, clock, CHAT, C, B) is None


def test_duels_in_different_chats_do_not_block_each_other(store, clock) -> None:
    assert duels.challenge(store, clock, CHAT, A, B) is not None
    assert duels.challenge(store, clock, OTHER, A, B) is not None


def test_finished_duel_frees_the_chat(store, clock) -> None:
    duel = _accepted(store, clock)
    duels.finish(store, clock, duel, [(6, 1), (6, 1), (6, 1)])
    assert duels.challenge(store, clock, CHAT, A, B) is not None


@pytest.mark.parametrize("respond", [duels.accept, duels.decline])
def test_only_the_opponent_may_answer(store, clock, respond) -> None:
    duel_id = duels.challenge(store, clock, CHAT, A, B)
    for stranger in (A, C):
        assert respond(store, clock, CHAT, duel_id, stranger) is duels.Answer.NOT_OPPONENT
    assert store.get_duel(CHAT, duel_id).state == "pending"


def test_answer_from_another_chat_finds_no_duel(store, clock) -> None:
    duel_id = duels.challenge(store, clock, CHAT, A, B)
    assert duels.accept(store, clock, OTHER, duel_id, B) is duels.Answer.GONE
    assert store.get_duel(CHAT, duel_id).state == "pending"


def test_second_accept_does_not_start_a_second_bout(store, clock) -> None:
    duel = _accepted(store, clock)
    assert duels.accept(store, clock, CHAT, duel.id, B) is duels.Answer.GONE


def test_decline_after_accept_is_refused(store, clock) -> None:
    duel = _accepted(store, clock)
    assert duels.decline(store, clock, CHAT, duel.id, B) is duels.Answer.GONE
    assert store.get_duel(CHAT, duel.id).state == "active"


def test_decline_frees_the_chat_without_points(store, clock) -> None:
    duel_id = duels.challenge(store, clock, CHAT, A, B)
    assert duels.decline(store, clock, CHAT, duel_id, B) is duels.Answer.OK
    assert store.get_duel(CHAT, duel_id).state == "declined"
    assert store.balance(CHAT, A) == store.balance(CHAT, B) == 0
    assert duels.challenge(store, clock, CHAT, A, B) is not None


def test_late_accept_is_refused_before_the_sweep_runs(store, clock) -> None:
    duel_id = duels.challenge(store, clock, CHAT, A, B)
    clock.set(NOON + duels.ANSWER_TIME)
    assert duels.accept(store, clock, CHAT, duel_id, B) is duels.Answer.GONE


async def test_unanswered_challenge_expires_and_its_message_says_so(store, clock) -> None:
    duel_id = duels.challenge(store, clock, CHAT, A, B)
    store.set_duel_message(CHAT, duel_id, 77)
    editor = FakeEditor()
    clock.set(NOON + duels.ANSWER_TIME - timedelta(seconds=1))
    await duel_sweep.tick(store, clock, editor)
    assert editor.edits == []
    clock.set(NOON + duels.ANSWER_TIME)
    await duel_sweep.tick(store, clock, editor)
    await duel_sweep.tick(store, clock, editor)
    assert editor.edits == [(CHAT, 77, texts.DUEL_EXPIRED.format(b="Boris"))]
    assert store.get_duel(CHAT, duel_id).state == "expired"
    assert store.balance(CHAT, A) == store.balance(CHAT, B) == 0


async def test_lost_bout_stops_holding_the_chat(store, clock) -> None:
    # A restart in the middle of a bout must not block duels in this chat forever.
    duel = _accepted(store, clock)
    store.set_duel_message(CHAT, duel.id, 77)
    editor = FakeEditor()
    clock.set(NOON + duels.BOUT_TIME)
    await duel_sweep.tick(store, clock, editor)
    assert editor.edits == [(CHAT, 77, texts.DUEL_ABORTED)]
    assert duels.challenge(store, clock, CHAT, A, B) is not None


async def test_bout_ending_after_the_sweep_pays_nothing(store, clock) -> None:
    duel = _accepted(store, clock)
    clock.set(NOON + duels.BOUT_TIME)
    await duel_sweep.tick(store, clock, FakeEditor())
    assert duels.finish(store, clock, duel, [(6, 1), (6, 1), (6, 1)]) is None
    assert store.balance(CHAT, A) == 0


async def test_undeliverable_message_does_not_stop_the_sweep(store, clock) -> None:
    first = duels.challenge(store, clock, CHAT, A, B)
    second = duels.challenge(store, clock, OTHER, A, B)
    store.set_duel_message(CHAT, first, 77)
    store.set_duel_message(OTHER, second, 78)
    clock.set(NOON + duels.ANSWER_TIME)
    await duel_sweep.tick(store, clock, FakeEditor(fail=True))
    assert store.get_duel(CHAT, first).state == "expired"
    assert store.get_duel(OTHER, second).state == "expired"


async def test_sweep_skips_chats_that_are_switched_off(store, clock) -> None:
    new_chat = -9003
    store.enable_chat(new_chat, NOON, "Europe/Moscow")
    duels.challenge(store, clock, CHAT, A, B)
    store.migrate_chat(CHAT, new_chat)  # refused: only switches CHAT off
    clock.set(NOON + duels.ANSWER_TIME)
    assert duels.sweep(store, clock) == []


def test_tied_rounds_are_replayed() -> None:
    assert not duels.bout_over([(6, 1), (3, 3), (6, 1)])
    assert duels.bout_over([(6, 1), (3, 3), (6, 1), (1, 6)])


def test_three_decisive_rounds_are_played_even_at_two_nil() -> None:
    assert not duels.bout_over([(6, 1), (6, 1)])


def test_all_ties_end_at_the_round_limit() -> None:
    assert duels.bout_over([(4, 4)] * duels.MAX_ROUNDS)
    assert not duels.bout_over([(4, 4)] * (duels.MAX_ROUNDS - 1))


def test_winner_gets_points_through_the_ledger_once(store, clock) -> None:
    duel = _accepted(store, clock)
    duels.finish(store, clock, duel, [(1, 6), (6, 1), (2, 5)])
    assert store.balance(CHAT, B) == duels.WIN_POINTS
    assert store.balance(CHAT, A) == 0
    assert duels.finish(store, clock, duel, [(1, 6), (6, 1), (2, 5)]) is None
    assert store.balance(CHAT, B) == duels.WIN_POINTS


def test_draw_at_the_round_limit_pays_nobody(store, clock) -> None:
    duel = _accepted(store, clock)
    assert duels.finish(store, clock, duel, [(4, 4)] * duels.MAX_ROUNDS) == []
    assert store.balance(CHAT, A) == store.balance(CHAT, B) == 0
    assert store.get_duel(CHAT, duel.id).state == "done"
    assert achievements.DUELIST not in store.achievements_of(CHAT, A)


def test_first_win_gives_duelist_once(store, clock) -> None:
    duel = _accepted(store, clock)
    assert duels.finish(store, clock, duel, [(6, 1)] * 3) == [achievements.DUELIST]
    duel = _accepted(store, clock)
    assert duels.finish(store, clock, duel, [(6, 1)] * 3) == []
    assert store.achievements_of(CHAT, A) == [achievements.DUELIST]


def test_finish_marks_the_dashboard(store, clock) -> None:
    duel = _accepted(store, clock)
    store.clear_dashboard_dirty(CHAT)
    duels.finish(store, clock, duel, [(6, 1)] * 3)
    assert CHAT in store.dirty_dashboard_chats()


def test_live_duel_moves_with_a_migrated_chat(store, clock) -> None:
    new_chat = -9004
    duel = _accepted(store, clock)
    assert store.migrate_chat(CHAT, new_chat)
    assert store.get_duel(new_chat, duel.id).state == "active"
    assert duels.challenge(store, clock, new_chat, A, B) is None
