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
    "/pigeon stats [week] — рейтинг и задание недели\n"
    "/pigeon stats all — общий рейтинг и Золотой плевок\n"
    "/pigeon profile — твои очки, ачивки и трофей\n"
    "/pigeon fortune — личное предсказание, раз в день\n\n"
    "<b>Для админов</b>\n"
    "/pigeon setup — открыть голубятню, выбрать часовой пояс\n"
    "/pigeon quiet [с] [до] — тихие часы, например /pigeon quiet 23 9\n"
    "/pigeon pause 24h, 7d или off — пауза, голубятня ничего не пишет сама\n"
    "/pigeon adult on или off — редкие голубки с подарком 18+\n"
    "/pigeon arrive — выпустить голубя\n\n"
    "<b>Как это работает</b>\n"
    "Голуби прилетают сами до трёх раз в неделю, в случайные дни, днём по часовому поясу чата. "
    "Каждый день голубятня сама публикует предсказание; по понедельникам — итоги и задание недели. "
    "Одна попытка плевка может принести 0–3 очка. Первый, кто попал, забирает Золотой плевок. "
    "У голубей бывают кепка, сонный или золотой вид; золотой удваивает очки. "
    "Чем больше людей прогоняет голубя, тем вероятнее он улетит.\n\n"
    "<b>Приватность</b>\n"
    "Текст сообщений чата и изображения бот не сохраняет. "
    "Хранятся настройки чата, id и имена игроков, время, игровые действия, очки и ачивки."
)
# Shown only when the weekly chronicle is on (ADR-0014): names leave the bot then.
CHRONICLE_PRIVACY = (
    "\nДля недельной хроники раз в неделю имена игроков и игровая статистика прошлой недели "
    "передаются внешнему сервису OpenCode Go. Текст сообщений туда не передаётся."
)


def help_text(chronicle_enabled: bool = False) -> str:
    return HELP + (CHRONICLE_PRIVACY if chronicle_enabled else "")


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
ADULT_ON = (
    "🔞 Режим 18+ включён. Иногда будет прилетать голубка с секретным подарком: "
    "победитель сможет показать в чат откровенное фото под спойлером."
)
ADULT_OFF = "Режим 18+ выключен. Новых голубок с подарком и показов наград не будет."
ADULT_BAD = "Укажи /pigeon adult on или /pigeon adult off"
HIT_BAD_AREA = "Здесь нужно выбрать одну из кнопок с областями."
HIT_FALLBACK = "Попал!"  # a hit without content lines, e.g. in tests
MISS_FALLBACK = "Мимо!"
AREA_NAMES = {"cap": "кепка", "wing": "крыло", "tail": "хвост"}
AREA_BUTTONS = {"cap": "🧢 Кепка", "wing": "🪽 Крыло", "tail": "🪶 Хвост"}
GIFT_BUTTON = "🎁 Показать подарок 18+"
GIFT_CAPTION = "🔞 Подарок от голубки. Контент 18+."
GIFT_NOT_WINNER = "Этот подарок достался другому участнику."
GIFT_UNAVAILABLE = "Подарок потерялся по дороге. Попробуй ещё раз."
GIFT_IN_PROGRESS = "Подарок уже загружается."
GIFT_SENT = "Подарок уже показан."
GIFT_DISABLED = "Режим 18+ уже выключен."
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


def arrival(
    name: str,
    intro: str,
    shoos: Sequence[ShooLine],
    count: int,
    pigeon_type: str = "normal",
    actions: list[dict] | None = None,
    target: str | None = None,
) -> str:
    if pigeon_type == "adult":
        area = AREA_NAMES[target]
        lines = [
            f"🕊 Голубка {_bold(name)} прилетела с секретным подарком. Курлык!",
            f"🔞 Попади точно в область: <b>{area}</b>. Первый точный плевок получает подарок.",
        ]
    else:
        lines = ["🕊 " + _esc(intro).format(name=_bold(name))]
    type_lines = {
        "capped": "🧢 В кепке. Первый плевок только собьёт её.",
        "sleepy": "💤 Сонный. Прогнать его проще.",
        "golden": "✨ Золотой. Очки ×2, улетит через 5 минут.",
    }
    if pigeon_type in type_lines:
        lines.append(type_lines[pigeon_type])
    lines.append("")
    if actions is not None:
        for action in actions:
            if action["kind"] == "hit":
                icon = "💦" if action["points"] else "💨"
                points = f" +{action['points']}" if action["points"] else ""
                lines.append(f"{icon} {_bold(action['name'])}: {_esc(action['reaction'])}{points}")
            else:
                lines.append(
                    f"📣 {_bold(action['name'])}: {_esc(action['action'])} "
                    f"<i>{_esc(action['reaction'])}</i>"
                )
        if actions:
            lines.append("")
        lines.append(f"💦 Попаданий: <b>{count}</b>")
        return "\n".join(lines)
    if shoos:
        lines += [_shoo_line(shoo) for shoo in shoos[-SHOO_LOG_SIZE:]]
        lines.append("")
    lines.append(f"💦 Попаданий: <b>{count}</b>")
    return "\n".join(lines)


def flown(name: str, shoos: Sequence[ShooLine], count: int, pigeon_type: str = "normal") -> str:
    scared_by = next((shoo for shoo in shoos if shoo.flew), None)
    if pigeon_type == "adult":
        lines = [f"🕊 Голубка {_bold(name)} улетела.", ""]
    else:
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


def status_sitting(name: str, count: int, pigeon_type: str = "normal") -> str:
    if pigeon_type == "adult":
        return f"🕊 Голубка {_bold(name)} сидит на подоконнике. Попаданий: <b>{count}</b>."
    return f"🕊 Голубь {_bold(name)} сидит на подоконнике. Попаданий: <b>{count}</b>."


def render_fortune(item, name: str = "") -> str:
    titles = {
        "classic": "🔮 Предсказание дня",
        "omen": "🌤 Голубиная примета",
        "scene": "🕊 Весть с подоконника",
        "spread": "🔮 Расклад от голубя",
        "chat": "🏘 Прогноз для района",
        "serial": f"📜 Пророчество, часть {item.part}",
    }
    body = item.text.format(name=name) if item.format == "scene" else item.text
    return f"<b>{_esc(titles[item.format])}</b>\n<i>{_esc(body)}</i>"


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


def profile(
    name: str,
    balance: int,
    achievements: list[str],
    trophy_days: int,
    week_points: int | None = None,
) -> str:
    earned = ", ".join(_esc(item) for item in achievements) if achievements else "пока нет"
    return "\n".join(
        [
            f"👤 {_bold(name)}",
            f"Очки: <b>{balance}</b>",
            *([f"Очки за неделю: <b>{week_points}</b>"] if week_points is not None else []),
            f"🏅 Ачивки: {earned}",
            f"🏆 Золотой плевок: {trophy_days} дн.",
        ]
    )


def week_stats(rows, quest: str, title: str | None) -> str:
    lines = ["📊 <b>Рейтинг недели</b>"]
    lines += (
        [f"{i}. {_esc(name)} — {points}" for i, (name, points) in enumerate(rows, 1)]
        if rows
        else ["Пока пусто."]
    )
    if quest:
        lines += ["", quest]
    if title:
        lines.append(f"🏅 Титул чата: {_esc(title)}")
    return "\n".join(lines)
