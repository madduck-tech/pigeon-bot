# Реестр архитектурных и продуктовых решений

ADR фиксируют значимые решения до реализации. Правила их применения описаны в [процессе разработки](../development-flow.md) и [CLAUDE.md](../../CLAUDE.md).

## Статусы

- `proposed` — предложение обсуждается; реализация ещё не разрешена.
- `accepted` — решение принято и обязательно к соблюдению.
- `rejected` — предложение отклонено, документ сохранён для истории.
- `superseded` — решение заменено новым ADR, указанным в поле `Superseded by`.

Принятый ADR не переписывается по смыслу. Переопределение выполняется новым ADR после требуемого согласования.

ADR-0002–0011 зарегистрированы ретроспективно 2026-11-06: они фиксируют уже принятые и реализованные решения по `design.md`, коду и эксплуатационной документации, а не разрешают новую разработку. Дата в таких документах обозначает дату регистрации решения в реестре.

## Реестр

| ADR | Решение | Дата | Статус | Связь |
|---|---|---|---|---|
| [0001](0001-diversify-pigeon-experience.md) | Разнообразие предсказаний и прилётов | 2026-11-05 | accepted | Частично пересмотрен [0012](0012-adult-pigeon-gift.md) |
| [0002](0002-focus-on-dovecote.md) | Фокус продукта на Голубятне | 2026-11-06 | accepted | Уточняется [0001](0001-diversify-pigeon-experience.md) |
| [0003](0003-chat-isolation-and-migration.md) | Изоляция чатов и перенос `chat_id` без слияния | 2026-11-06 | accepted | Частично пересмотрен [0018](0018-usage-statistics.md) |
| [0004](0004-privacy-and-minimal-data.md) | Приватность, минимум данных и отсутствие пингов | 2026-11-06 | accepted | — |
| [0005](0005-layered-monolith-sqlite.md) | Слоистый монолит, SQLite и инжектируемые время и случайность | 2026-11-06 | accepted | — |
| [0006](0006-single-message-arrival-loop.md) | Игровой цикл в одном редактируемом сообщении | 2026-11-06 | accepted | Детали развития в [0001](0001-diversify-pigeon-experience.md); частично пересмотрен [0012](0012-adult-pigeon-gift.md) |
| [0007](0007-local-schedule-and-noise-budget.md) | Локальное расписание, бюджет шума и повторы отправки | 2026-11-06 | accepted | Ограничения развития в [0001](0001-diversify-pigeon-experience.md) |
| [0008](0008-group-commands-and-admin-setup.md) | Единая команда и явная административная настройка | 2026-11-06 | accepted | Частично пересмотрен [0012](0012-adult-pigeon-gift.md) и [0013](0013-separate-bot-commands.md) |
| [0009](0009-yaml-content-startup-validation.md) | YAML-контент с проверкой при старте | 2026-11-06 | accepted | Схемы R1–R3 в [0001](0001-diversify-pigeon-experience.md) |
| [0010](0010-html-rendering-and-escaping.md) | HTML-разметка и централизованное экранирование | 2026-11-06 | accepted | — |
| [0011](0011-container-release-without-cd.md) | Проверенный Docker-образ и внешний деплой без CD | 2026-11-06 | accepted | — |
| [0012](0012-adult-pigeon-gift.md) | Голубка с подарком 18+ по явному включению чата | 2026-11-06 | accepted | Частично пересматривает [0001](0001-diversify-pigeon-experience.md) §2, [0006](0006-single-message-arrival-loop.md) и [0008](0008-group-commands-and-admin-setup.md) |
| [0013](0013-separate-bot-commands.md) | Отдельные команды вместо подкоманд `/pigeon` | 2026-11-08 | accepted | Частично пересматривает [0008](0008-group-commands-and-admin-setup.md) |
| [0014](0014-weekly-llm-chronicle.md) | Недельная хроника от LLM в понедельничном посте | 2026-11-08 | accepted | Дополняет [0004](0004-privacy-and-minimal-data.md) (имена уходят внешнему сервису) |
| [0015](0015-agent-test-stand.md) | Тестовый стенд в Telegram с синтетическими апдейтами | 2026-11-08 | accepted | — |

| [0016](0016-fortune-courier.md) | Утренний голубь-предсказатель без многочастных пророчеств | 2026-11-12 | accepted | Частично пересматривает [0001](0001-diversify-pigeon-experience.md), [0007](0007-local-schedule-and-noise-budget.md), [0009](0009-yaml-content-startup-validation.md) |

| [0017](0017-manual-arrival-replaces-daily-auto.md) | Ручной игровой прилёт заменяет автоматический до конца дня | 2026-11-13 | accepted | Частично пересматривает [0007](0007-local-schedule-and-noise-budget.md) |
| [0018](0018-usage-statistics.md) | Статистика использования в SQLite | 2026-11-13 | accepted | [Issue #20](https://github.com/madduck-tech/pigeon-bot/issues/20) |

| [0019](0019-setup-invitation.md) | Приглашение к настройке после добавления бота | 2026-11-13 | accepted | Частично пересматривает [0007](0007-local-schedule-and-noise-budget.md), #14 |

## Новый документ

1. Скопируйте [TEMPLATE.md](TEMPLATE.md) в `NNNN-short-title.md`, используя следующий последовательный номер.
2. Заполните контекст, решение, последствия и альтернативы.
3. Добавьте строку в реестр со статусом `proposed` и откройте PR.
4. После согласования установите `accepted` до начала реализации.
5. Обновите `docs/design.md` до изменения кода.

Шаблон сам не является решением и не включается в реестр.
