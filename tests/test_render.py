"""Every message is HTML, so a name must not be able to break it or add markup."""

from types import SimpleNamespace

from aiogram.enums import ParseMode
from aiogram.utils.text_decorations import html_decoration

from pigeon.app import build_bot
from pigeon.render import texts

NASTY = "<b>Вася & Co</b> {name} vasya_2000*"
SAFE = "&lt;b&gt;Вася &amp; Co&lt;/b&gt; {name} vasya_2000*"


def _shoo(name=NASTY, flew=False):
    return SimpleNamespace(name=name, action="топ <ногой>!", reaction="сидит & смотрит", flew=flew)


def test_bot_sends_html_by_default() -> None:
    assert build_bot("42:TEST").default.parse_mode == ParseMode.HTML


def test_arrival_escapes_names_and_content() -> None:
    text = texts.arrival(NASTY, "Голубь {name} & кепка", [_shoo()], 2)
    assert f"Голубь <b>{SAFE}</b> &amp; кепка" in text
    assert f"📣 <b>{SAFE}</b>: топ &lt;ногой&gt;! <i>сидит &amp; смотрит</i>" in text
    assert "Попаданий: <b>2</b>" in text


def test_arrival_shows_only_the_last_attempts() -> None:
    shoos = [_shoo(name=f"u{i}") for i in range(texts.SHOO_LOG_SIZE + 2)]
    text = texts.arrival("Гена", "Голубь {name}", shoos, 0)
    assert "u0" not in text and "u1" not in text
    assert all(f"u{i}" in text for i in range(2, texts.SHOO_LOG_SIZE + 2))


def test_flown_names_who_scared_the_pigeon() -> None:
    text = texts.flown("Гена", [_shoo(name="Боря"), _shoo(name="Маша", flew=True)], 3)
    assert "Голубь <b>Гена</b> улетел." in text
    assert "Кто спугнул: <b>Маша</b>" in text
    assert "Попаданий: <b>3</b>" in text
    assert "Боря" not in text


def test_flown_on_its_own() -> None:
    text = texts.flown("Гена", [_shoo(name="Боря")], 0)
    assert "Улетел сам" in text and "Кто спугнул" not in text


def test_stats_escape_names_and_show_the_trophy() -> None:
    text = texts.stats([(NASTY, 3)], NASTY, 2)
    assert f"Золотой плевок: <b>{SAFE}</b> (держит 2 дн.)" in text
    assert f"1. {SAFE} — 3" in text


def test_empty_stats() -> None:
    text = texts.stats([], None, 0)
    assert "пока никто не попадал" in text and "Пока пусто" in text


def test_profile_escapes_the_name() -> None:
    assert f"<b>{SAFE}</b>" in texts.profile(NASTY, 1, [], 0)


def test_fortune_is_escaped() -> None:
    assert texts.fortune("а < б") == "🔮 <i>а &lt; б</i>"


def test_markup_is_plain_tags_only_so_nobody_is_pinged() -> None:
    samples = [
        texts.HELP,
        texts.arrival("Гена", "Голубь {name}", [_shoo(name="Маша")], 1),
        texts.flown("Гена", [_shoo(name="Маша", flew=True)], 1),
        texts.stats([("Маша", 1)], "Маша", 1),
    ]
    assert not any("<a " in sample or "tg://" in sample for sample in samples)
    assert html_decoration.bold("x") == "<b>x</b>"
