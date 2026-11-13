"""Local read-only usage report: python -m tools.stats --db PATH --days 7."""

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from pigeon.clock import SystemClock
from pigeon.repo.usage import read_report


def render(report: dict) -> str:
    lines = [
        f"Статистика: {report['start']} — {report['end']} (UTC, текущий день неполный)",
        f"Начало сбора новых метрик: {report['started_at'] or 'нет данных; сбор ещё не начат'}",
        "До начала сбора история новых метрик недоступна; отсутствие данных не означает ноль.",
        "При сбоях возможны потери и повторы. Новые данные: окно до 90 UTC-дней; "
        "при простое/сбое очистка откладывается. Игровая история хранится отдельно.",
        f"Включённых чатов сейчас: {report['enabled_chats']}",
        f"Чатов с участником за 7 календарных UTC-дней: {report['active_chats_7d']}",
    ]
    totals = Counter()
    for row in report["days"]:
        totals.update({k: v for k, v in row["metrics"].items() if k != "participants"})
    lines.append("Сводка периода: " + _counts(totals))
    for chat, n in sorted(report["participants"].items()):
        lines.append(f"Чат {chat}: уникальных участников за период: {n}")
    for row in report["days"]:
        lines.append(f"Чат {row['chat_id']} / {row['utc_day']} UTC: {_counts(row['metrics'])}")
    lines.append(
        f"Личные предсказания — отдельно за {report['local_days']} локальных дней, "
        "включая текущий день по нынешнему поясу чата (local_day, не UTC-дата; "
        "исторический пояс после смены не восстанавливается):"
    )
    for row in report["local_fortunes"]:
        lines.append(f"Чат {row['chat_id']} / {row['local_day']} local: {row['count']}")
    if not report["days"] and not report["local_fortunes"]:
        lines.append("За выбранный период сохранённых данных нет.")
    return "\n".join(lines)


def _counts(metrics) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(metrics.items())) or "нет данных"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--days", type=int, default=7, choices=range(1, 91), metavar="1..90")
    parser.add_argument("--chat-id", type=int)
    args = parser.parse_args(argv)
    try:
        report = read_report(args.db, SystemClock().now(), args.days, args.chat_id)
    except (sqlite3.Error, OSError, ValueError):
        print(
            "Не удалось прочитать базу. Нужен существующий файл с доступом на чтение "
            "и схемой версии с ADR-0018; инструмент не создаёт базу и не запускает миграции.",
            file=sys.stderr,
        )
        return 2
    print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
