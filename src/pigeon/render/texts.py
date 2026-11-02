"""User-facing texts (Russian) and their HTML markup. Messages go out with parse_mode=HTML, so
everything that comes from outside or from content/ is escaped here. Toasts are plain text."""

from collections.abc import Sequence
from html import escape as _html_escape
from typing import Protocol

HELP = (
    "🕊 <b>Голубятня</b>\n"
    "Иногда выпускает в чат голубя. В него можно плюнуть, а можно прогнать.\n\n"
    "<b>Команды</b>\n"
    "/pigeon — кто сейчас на подоконнике\n"
    "/pigeon stats — Золотой плевок и рейтинг\n"
    "/pigeon profile — твои очки, ачивки и трофей\n"
    "/pigeon fortune — личное предсказание, раз в день\n\n"
    "<b>Для админов</b>\n"
    "/pigeon setup — открыть голубятню, выбрать часовой пояс\n"
    "/pigeon quiet [с] [до] — тихие часы, например /pigeon quiet 23 9\n"
    "/pigeon pause 24h, 7d или off — пауза, голубятня ничего не пишет сама\n"
    "/pigeon arrive — выпустить голубя\n\n"
    "<b>Как это работает</b>\n"
    "Голуби прилетают сами до трёх раз в неделю, в случайные дни, днём по часовому поясу чата. "
    "Каждый день голубятня сама публикует предсказание дня. "
    "Первый, кто попал, забирает Золотой плевок. "
    "Чем больше людей прогоняет голубя, тем вероятнее он улетит.\n\n"
    "<b>Приватность</b>\n"
    "Текст сообщений чата бот не сохраняет. "
    "Хранятся только id и имена игроков, время, очки и ачивки."
)

NOT_GROUP = "Голубятня работает только в групповых чатах."
NOT_ENABLED = "Сначала админ чата должен выполнить /pigeon setup."
ADMIN_ONLY = "Эта команда только для админов чата."
SETUP_DONE = (
    "🕊 Голубятня открыта. Часовой пояс чата: {tz}. Голуби будут прилетать сами, днём. "
    "Выпустить голубя раньше: /pigeon arrive."
)
SETUP_CHOOSE = "🕊 <b>Голубятня</b>\nВыбери часовой пояс чата. Нажать может только админ."
SETUP_CHOOSE_CURRENT = (
    "🕊 <b>Голубятня</b>\nСейчас часовой пояс чата: {tz}. Сменить может только админ."
)
TZ_BAD = (
    "Не знаю такого часового пояса. "
    "Напиши /pigeon setup без аргумента и выбери кнопкой или укажи, например, Europe/Moscow."
)
TZ_UNKNOWN = "Этого варианта уже нет. Запусти /pigeon setup ещё раз."
QUIET_SET = "Тихие часы: с {start}:00 до {end}:00 по часовому поясу чата."
QUIET_BAD = "Укажи два часа от 0 до 23, и чтобы «до» было раньше «с». Пример: /pigeon quiet 23 9"
PIGEON_NAME = "Витёк"  # arrivals opened before pigeons had their own names
ARRIVAL_INTRO = "Голубь {name} прилетел на подоконник. Курлык!"
ALREADY_SITTING = "Голубь уже сидит на подоконнике. Плюйте, пока не улетел."
STATUS_AWAY = "🏠 Все голуби в голубятне. Админ может выпустить одного: /pigeon arrive."
HIT_OK = "💦 Попал!"
HIT_ALREADY = "По этому голубю у тебя уже есть плевок."
HIT_CLOSED = "Голубь улетел."
HIT_NO_EVENT = "Этот голубь уже не тот."
SHOO_STAYED = "📣 Голубь остался на месте."
SHOO_FLEW = "📣 Голубь улетел!"
SHOO_ALREADY = "Одна попытка на голубя, и она уже была."
BUTTON_RETIRED = "Эта кнопка больше не работает."
PLUCK_BUTTON = "💦 Плюнуть"
SHOO_BUTTON = "📣 Прогнать"
PAUSE_SET = "Пауза до {until} по часовому поясу чата. Голубятня сама ничего не пишет."
PAUSE_OFF = "Пауза снята."
PAUSE_BAD = "Пример: /pigeon pause 24h, /pigeon pause 7d или /pigeon pause off"
FORTUNE_ALREADY = "Голубь уже всё сказал на сегодня. Приходи завтра."
SHOO_LOG_SIZE = 3


class ShooLine(Protocol):
    name: str
    action: str
    reaction: str
    flew: bool


def _esc(text: str) -> str:
    return _html_escape(text, quote=False)


def _bold(text: str) -> str:
    return f"<b>{_esc(text)}</b>"


def _shoo_line(shoo: ShooLine) -> str:
    return f"📣 {_bold(shoo.name)}: {_esc(shoo.action)} <i>{_esc(shoo.reaction)}</i>"


def arrival(name: str, intro: str, shoos: Sequence[ShooLine], count: int) -> str:
    lines = ["🕊 " + _esc(intro).format(name=_bold(name)), ""]
    if shoos:
        lines += [_shoo_line(shoo) for shoo in shoos[-SHOO_LOG_SIZE:]]
        lines.append("")
    lines.append(f"💦 Попаданий: <b>{count}</b>")
    return "\n".join(lines)


def flown(name: str, shoos: Sequence[ShooLine], count: int) -> str:
    scared_by = next((shoo for shoo in shoos if shoo.flew), None)
    lines = [f"🕊 Голубь {_bold(name)} улетел.", ""]
    if scared_by is None:
        lines += [f"💦 Попаданий: <b>{count}</b>", "💤 Улетел сам, никто не спугнул."]
    else:
        lines += [
            _shoo_line(scared_by),
            f"💦 Попаданий: <b>{count}</b>",
            f"📣 Кто спугнул: {_bold(scared_by.name)}",
        ]
    return "\n".join(lines)


def status_sitting(name: str, count: int) -> str:
    return f"🕊 Голубь {_bold(name)} сидит на подоконнике. Попаданий: <b>{count}</b>."


def fortune(line: str) -> str:
    return f"🔮 <i>{_esc(line)}</i>"


def daily_fortune(line: str) -> str:
    return f"🔮 <b>Предсказание дня</b>\n<i>{_esc(line)}</i>"


def stats(rows: list[tuple[str, int]], holder: str | None, holder_days: int) -> str:
    if holder:
        trophy = f"🏆 Золотой плевок: {_bold(holder)} (держит {holder_days} дн.)"
    else:
        trophy = "🏆 Золотой плевок: пока никто не попадал"
    lines = [trophy, "", "📊 <b>Рейтинг</b>"]
    if rows:
        lines += [f"{i}. {_esc(name)} — {points}" for i, (name, points) in enumerate(rows, start=1)]
    else:
        lines.append("Пока пусто. Прилетит голубь — будет рейтинг.")
    return "\n".join(lines)


def with_awards(text: str, names: list[str]) -> str:
    """A toast with the achievements just earned."""
    return "\n".join([text, *(f"🏅 Ачивка: {name}" for name in names)])


def profile(name: str, balance: int, achievements: list[str], trophy_days: int) -> str:
    earned = ", ".join(_esc(item) for item in achievements) if achievements else "пока нет"
    return "\n".join(
        [
            f"👤 {_bold(name)}",
            f"Очки: <b>{balance}</b>",
            f"🏅 Ачивки: {earned}",
            f"🏆 Золотой плевок: {trophy_days} дн.",
        ]
    )
