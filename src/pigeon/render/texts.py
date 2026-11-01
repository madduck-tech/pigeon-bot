"""User-facing texts (Russian). Kept apart from logic so they can change without code edits."""

HELP = (
    "🕊 Голубь — шуточный персонаж чата.\n\n"
    "Команды:\n"
    "/pigeon — статус\n"
    "/pigeon arrive — голубь прилетает (только админы)\n"
    "/pigeon stats — рейтинг плевков\n"
    "/pigeon setup [часовой пояс] — включить бота в этом чате (только админы)\n"
    "/pigeon quiet [с] [до] — тихие часы, например /pigeon quiet 23 9 (только админы)\n\n"
    "Голубь сам прилетает раз в день в дневное время по часовому поясу чата. "
    "Ночью бот молчит. Если чат оживлён, прилёт откладывается.\n\n"
    "Приватность: бот не сохраняет текст сообщений. "
    "Хранятся только id участников, время и очки."
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
STATS_HEADER = "📊 Рейтинг плевков:"


def stats(rows: list[tuple[str, int]]) -> str:
    lines = [STATS_HEADER]
    lines += [f"{i}. {name} — {points}" for i, (name, points) in enumerate(rows, start=1)]
    return "\n".join(lines)


def dashboard(
    arrivals: int,
    sitting: bool,
    holder: str | None,
    holder_days: int,
    top: list[tuple[str, int]],
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
    return "\n".join(
        [
            f"🕊 Голубь {PIGEON_NAME} · прилётов: {arrivals}",
            f"Статус: {status}",
            trophy,
            weekly,
        ]
    )
