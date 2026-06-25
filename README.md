# tg_board_games_bot

[![CI](https://github.com/boundlessend/tg_board_games_bot/actions/workflows/ci.yml/badge.svg)](https://github.com/boundlessend/tg_board_games_bot/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0)](https://aiogram.dev/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.x-D71F00)](https://www.sqlalchemy.org/)
[![SQLite](https://img.shields.io/badge/storage-SQLite-003B57)](https://www.sqlite.org/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-bot-26A5E4)](https://core.telegram.org/bots)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

НА-СТОЛ-КИ by senya

Telegram-бот с помощниками для настольных игр. Сейчас есть помощник «Опасные слова»: он выдаёт слова, проклятья и боссов без повторов для каждого Telegram ID и хранит историю в SQLite.

## Установка

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Настройка

```bash
cp .env.example .env
```

В `.env` укажите токен и (опционально) Telegram ID администраторов через запятую:

```env
BOT_TOKEN=your_real_telegram_bot_token
ADMIN_IDS=11111111,22222222
```

Без `ADMIN_IDS` админ-меню недоступно никому.

## Запуск

```bash
python bot.py
```

При первом запуске рядом с кодом создаётся `bot.sqlite3`.

### Docker

```bash
docker build -t tg-board-games .
docker run --env-file .env -v "$(pwd)/db:/db" -e DATABASE_PATH=/db/bot.sqlite3 tg-board-games
```

Бот рассчитан на запуск по требованию (под отдельные игры), работает на long polling. Том `-v "$(pwd)/db:/db"` + `DATABASE_PATH=/db/bot.sqlite3` сохраняют историю выдач между запусками, иначе она теряется вместе с контейнером. Путь `DATABASE_PATH` можно задать и в `.env`; если он пуст, база создаётся рядом с кодом (`./bot.sqlite3`).

## Данные

- `data/words.json` — 500 обычных и 500 фэнтези-слов.
- `data/curses.json` — 100 проклятий.
- `data/bosses.json` — 100 боссов.
- `data/crocodile.json`, `data/alias.json` — пулы слов для игр «Крокодил» и «Алиас».

Пулы можно расширять из админки (контент хранится в SQLite и добавляется к файловым словам при выдаче).

В сообщениях показывается счётчик выдачи: слова до `1000`, проклятья и боссы до `100`. Слова заканчиваются после 1000 уникальных выдач пользователю. Проклятья и боссы после исчерпания начинают новый круг.

Меню работает на inline-кнопках (сообщение редактируется на месте). Команда `/help` показывает справку.

В меню «Опасные слова» есть два режима:

- `Я ведущий` - слова, проклятья, боссы, отдельные сбросы каждого списка и кнопка `Новая игра (сбросить всё)`.
- `Я игрок` - слова, сброс слов и кнопка `Новая игра (сбросить всё)`.

Инлайн-режим: наберите `@имя_бота` в любом чате, чтобы получить случайные слова (без учёта истории).

## Админ-меню

Добавьте свой Telegram ID в `ADMIN_IDS` в `.env` и отправьте боту команду `/admin` в личном чате. Доступ к админ-меню есть только у ID из списка. Бот покажет сводку (число пользователей, суммы выдач, топ-10 слов). Кнопки: `Полный отчёт` - подробная статистика по каждому Telegram ID (длинный отчёт делится на несколько сообщений), `Выгрузить CSV` - статистика файлом, `Закрыть админку` - выход.

Команды добавления контента (только для админов, в SQLite):

- `/addword <игра> <слово>` - игры: `dangerous_words`, `crocodile`, `alias`.
- `/addcurse <название> | <описание>`
- `/addboss <имя> | <описание>`

## Добавление игр

Простая словесная игра (как «Крокодил»/«Алиас»):

1. Положите файл `data/<game>.json` вида `{"words": [...]}`.
2. Добавьте запись в `_WORD_GAME_FILES` в `services/random_generator.py`.

Кнопка в главном меню, меню игры, выдача без повторов и авто-цикл подключаются автоматически через `handlers/word_games.py`.

Сложная игра со своей механикой (как «Опасные слова»): создайте роутер в `handlers/` и подключите его в `bot.py`; выдачу без повторов можно переиспользовать из `services/random_generator.py`.

## Лицензия

MIT. Подробности в `LICENSE`.
