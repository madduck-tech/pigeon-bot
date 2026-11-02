"""User-facing texts (Russian). Kept apart from logic so they can change without code edits."""

HELP = (
    "🕊 Голубь — шуточный персонаж чата.\n\n"
    "Команды:\n"
    "/pigeon — статус\n"
    "/pigeon arrive — голубь прилетает (только админы)\n"
    "/pigeon stats — рейтинг плевков\n"
    "/pigeon profile — твои очки, ачивки и трофей\n"
    "/pigeon fortune — предсказание, раз в день\n"
    "/pigeon duel — дуэль: в ответ на сообщение соперника или с его @username\n"
    "/pigeon photo — в ответ на фото: это голубь или ворона?\n"
    "/pigeon name — голосование за имя голубя (только админы)\n"
    "/pigeon setup [часовой пояс] — включить бота в этом чате (только админы)\n"
    "/pigeon quiet [с] [до] — тихие часы, например /pigeon quiet 23 9 (только админы)\n"
    "/pigeon pause 24h, 7d или off — пауза, голубь ничего не пишет сам (только админы)\n\n"
    "Голубь сам прилетает до трёх раз в неделю, в случайные дни, днём по часовому поясу чата. "
    "Ночью бот молчит. Если чат оживлён, прилёт откладывается.\n\n"
    "Первого числа голубю выбирают новое имя. "
    "Если написать «голубь» в подписи к фото, бот спросит, голубь ли это.\n\n"
    "Приватность: бот видит сообщения чата, но не сохраняет их текст. "
    "Хранятся только id, имена и username участников, время, очки и ачивки."
)

NOT_GROUP = "Голубь живёт в групповых чатах."
NOT_ENABLED = "Сначала админ чата должен выполнить /pigeon setup."
ADMIN_ONLY = "Эта команда только для админов чата."
SETUP_DONE = (
    "Готово. Часовой пояс чата: {tz}. Голубь будет прилетать сам днём. "
    "Прилететь раньше можно и вручную: /pigeon arrive."
)
TZ_BAD = "Не знаю такого часового пояса. Пример: /pigeon setup Europe/Moscow"
QUIET_SET = "Тихие часы: с {start}:00 до {end}:00 по часовому поясу чата."
QUIET_BAD = "Укажи два часа от 0 до 23, и чтобы «до» было раньше «с». Пример: /pigeon quiet 23 9"
PIGEON_NAME = "Витёк"
ALREADY_SITTING = "Голубь уже сидит на подоконнике. Плюйте, пока не улетел."
NO_STATS = "Пока никто не попадал. Прилетит голубь — будет рейтинг."
STATUS_SITTING = "🕊 Голубь сидит на подоконнике. Попаданий: {count}."
STATUS_AWAY = "🕊 Голубь сейчас не сидит. Админ может вызвать его: /pigeon arrive."
HIT_OK = "💦 Попал!"
HIT_ALREADY = "Ты уже попадал в этого голубя."
HIT_CLOSED = "Голубь улетел."
HIT_NO_EVENT = "Этот голубь уже не тот."
ARRIVAL = "🕊 Голубь прилетел на подоконник. Кто первый?\nПопаданий: {count}"
ARRIVAL_FLEW = "🕊 Голубь улетел. Попаданий: {count}."
PLUCK_BUTTON = "💦 Плюнуть"
DART_BUTTON = "🎯 Дартс"
BET_GE3_BUTTON = "💰 3+ (ставка 1)"
BET_LT3_BUTTON = "💰 меньше 3 (ставка 1)"
DART_RESULT = "🎯 Бросок: {value}. Очков: +{points}."
DART_ALREADY = "Сегодня ты уже бросал дартс. Завтра снова."
DART_CLOSED = "Голубь улетел, мишень убрали."
BET_PLACED = "💰 Ставка принята: {choice}. Ставка 1 очко, выигрыш 2."
BET_ALREADY = "На этого голубя ты уже ставил."
BET_NO_BALANCE = "Для ставки нужно хотя бы 1 очко."
BET_CHOICE = {"ge3": "3 и больше плевков", "lt3": "меньше 3 плевков"}
BET_CLOSED = "Голубь улетел, ставки закрыты."
STATS_HEADER = "📊 Рейтинг плевков:"
PAUSE_SET = "Пауза до {until} по часовому поясу чата. Голубь сам ничего не пишет."
PAUSE_OFF = "Пауза снята."
PAUSE_BAD = "Пример: /pigeon pause 24h, /pigeon pause 7d или /pigeon pause off"
FORTUNE_ALREADY = "Голубь уже всё сказал на сегодня. Приходи завтра."
DUEL_USAGE = "Ответь командой на сообщение соперника или напиши: /pigeon duel @username"
DUEL_UNKNOWN = "Голубь такого не знает. Ответь командой на его сообщение."
DUEL_SELF = "С собой дуэлиться нельзя."
DUEL_BOT = "Боты на дуэли не выходят."
DUEL_BUSY = "В чате уже идёт дуэль. Подожди, пока закончится."
DUEL_CHALLENGE = "⚔️ {a} вызывает на дуэль: {b}. На ответ 10 минут."
DUEL_ACCEPT_BUTTON = "✅ Принять"
DUEL_DECLINE_BUTTON = "❌ Отказаться"
DUEL_NOT_YOURS = "Это не твоя дуэль."
DUEL_GONE = "Этот вызов уже не действует."
DUEL_FIGHT = "⚔️ Дуэль: {a} против {b}. Первым бросает {a}."
DUEL_DECLINED = "⚔️ {b} отказывается от дуэли. Бывает."
DUEL_EXPIRED = "⚔️ Вызов остался без ответа: {b} не пришёл на дуэль."
DUEL_ABORTED = "⚔️ Дуэль сорвалась. Очки никому не начислены."
DUEL_ANSWERED = "Принято."
DUEL_WIN = "🏆 Победитель: {name}, +{points} очка."
DUEL_DRAW = "🤝 Ничья. Очки никому не начислены."
PHOTO_QUESTION = "Это голубь или ворона?"
PHOTO_OPTIONS = ("Голубь", "Ворона", "Не знаю")
PHOTO_USAGE = "Ответь этой командой на фото."
PHOTO_COOLDOWN = "Голубь уже спрашивал. Не чаще раза в час."
NAME_QUESTION = "Как теперь зовут голубя?"
NAME_ALREADY = "Голосование за имя уже идёт."
NAME_NO_OPTIONS = "Свободные имена закончились."
NAME_RENAMED = "🕊 Голосование закрыто. Теперь голубя зовут {name}."
NAME_NO_VOTES = "🕊 Никто не проголосовал. Голубя по-прежнему зовут {name}."
QUEST_TITLE = "Почётный"
QUEST_DONE = "🎯 Квест недели выполнен: {hits}/{target}. Голубю присвоен титул «{title}»."
QUEST_FAILED = "🎯 Квест недели не выполнен: {hits}/{target}. Голубь не в обиде."
QUEST_BAR_CELLS = 8


def stats(rows: list[tuple[str, int]]) -> str:
    lines = [STATS_HEADER]
    lines += [f"{i}. {name} — {points}" for i, (name, points) in enumerate(rows, start=1)]
    return "\n".join(lines)


def duel_result(
    a: str,
    b: str,
    rounds: list[tuple[int, int]],
    score: tuple[int, int],
    winner: str | None,
    points: int,
) -> str:
    lines = [f"⚔️ Итог дуэли: {a} против {b}"]
    for number, (value_a, value_b) in enumerate(rounds, start=1):
        tie = ", ничья" if value_a == value_b else ""
        lines.append(f"Раунд {number}: {value_a} : {value_b}{tie}")
    lines.append(f"Счёт {score[0]} : {score[1]}")
    lines.append(DUEL_WIN.format(name=winner, points=points) if winner else DUEL_DRAW)
    return "\n".join(lines)


def with_awards(text: str, names: list[str]) -> str:
    return "\n".join([text, *(f"🏅 Ачивка: {name}" for name in names)])


def quest_result(done: bool, hits: int, target: int) -> str:
    if done:
        return QUEST_DONE.format(hits=hits, target=target, title=QUEST_TITLE)
    return QUEST_FAILED.format(hits=hits, target=target)


def quest_bar(progress: int, target: int) -> str:
    filled = min(progress * QUEST_BAR_CELLS // target, QUEST_BAR_CELLS)
    return "▓" * filled + "░" * (QUEST_BAR_CELLS - filled)


def profile(name: str, balance: int, achievements: list[str], trophy_days: int) -> str:
    earned = ", ".join(achievements) if achievements else "пока нет"
    return "\n".join(
        [
            f"👤 {name}",
            f"Очки: {balance}",
            f"🏅 Ачивки: {earned}",
            f"🏆 Золотой помёт: {trophy_days} дн.",
        ]
    )


def dashboard(
    arrivals: int,
    sitting: bool,
    holder: str | None,
    holder_days: int,
    top: list[tuple[str, int]],
    name: str = PIGEON_NAME,
    title: str | None = None,
    quest: tuple[int, int] | None = None,
) -> str:
    status = "сидит на подоконнике" if sitting else "улетел, ждёт следующего прилёта"
    if holder:
        trophy = f"🏆 Золотой помёт: {holder} (держит {holder_days} дн.)"
    else:
        trophy = "🏆 Золотой помёт: пока никто не попадал"
    if top:
        weekly = "📊 Топ недели: " + " · ".join(f"{name} {points}" for name, points in top)
    else:
        weekly = "📊 Топ недели: пока пусто"
    lines = [f"🕊 Голубь {name} · прилётов: {arrivals}", f"Статус: {status}", trophy]
    if quest is not None:
        progress, target = quest
        line = f"🎯 Квест недели: {quest_bar(progress, target)} {progress}/{target}"
        lines.append(f"{line} · титул: {title}" if title else line)
    lines.append(weekly)
    return "\n".join(lines)
