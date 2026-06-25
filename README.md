# tg_board_games_bot

[![CI](https://github.com/boundlessend/tg_board_games_bot/actions/workflows/ci.yml/badge.svg)](https://github.com/boundlessend/tg_board_games_bot/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0)](https://aiogram.dev/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.x-D71F00)](https://www.sqlalchemy.org/)
[![SQLite](https://img.shields.io/badge/storage-SQLite-003B57)](https://www.sqlite.org/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-bot-26A5E4)](https://core.telegram.org/bots)
[![License: BSD-3-Clause](https://img.shields.io/badge/license-BSD--3--Clause-green)](LICENSE)

НА-СТОЛ-КИ by senya

Telegram-бот с помощниками для настольных игр. Помощники: «Опасные слова» (слова, проклятья, боссы) и словесные игры «Крокодил», «Алиас», «Кто я?», «Шляпа». Контент выдаётся без повторов для каждого Telegram ID, история хранится в SQLite. Есть инлайн-режим и админ-меню со статистикой и добавлением контента.

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
- `data/crocodile.json`, `data/alias.json`, `data/whoami.json`, `data/hat.json` — пулы слов для словесных игр «Крокодил», «Алиас», «Кто я?», «Шляпа».

Пулы можно расширять из админки (контент хранится в SQLite и добавляется к файловым словам при выдаче).

В сообщениях показывается счётчик выдачи относительно размера пула (база: 1000 слов, по 100 проклятий и боссов; пул растёт за счёт добавленного админом контента). В «Опасных словах» слова после исчерпания пула заканчиваются, проклятья и боссы начинают новый круг. В «Крокодиле» и «Алиасе» слова после исчерпания идут на новый круг.

Меню работает на inline-кнопках (сообщение редактируется на месте). Команда `/help` показывает справку.

В меню «Опасные слова» есть два режима:

- `Я ведущий` - слова, проклятья, боссы, отдельные сбросы каждого списка и кнопка `Новая игра (сбросить всё)`.
- `Я игрок` - слова, сброс слов и кнопка `Новая игра (сбросить всё)`.

Инлайн-режим: наберите `@имя_бота` в любом чате, чтобы получить случайные слова (без учёта истории).

## Админ-меню

Добавьте свой Telegram ID в `ADMIN_IDS` в `.env` и отправьте боту команду `/admin` в личном чате. Доступ к админ-меню есть только у ID из списка. Бот покажет сводку (число пользователей, суммы выдач, топ-10 слов). Кнопки: `Полный отчёт` - подробная статистика по каждому Telegram ID (длинный отчёт делится на несколько сообщений), `Выгрузить CSV` - статистика файлом, `Закрыть админку` - выход.

Команды добавления контента (только для админов, в SQLite):

- `/addword <игра> <слово>` - игры: `dangerous_words`, `crocodile`, `alias`, `whoami`, `hat`.
- `/addcurse <название> | <описание>`
- `/addboss <имя> | <описание>`

## Добавление игр

Простая словесная игра (как «Крокодил»/«Алиас»):

1. Положите файл `data/<game>.json` вида `{"words": [...]}`.
2. Добавьте запись в `_WORD_GAME_FILES` в `services/random_generator.py`.

Кнопка в главном меню, меню игры, выдача без повторов и авто-цикл подключаются автоматически через `handlers/word_games.py`.

Сложная игра со своей механикой (как «Опасные слова»): создайте роутер в `handlers/` и подключите его в `bot.py`; выдачу без повторов можно переиспользовать из `services/random_generator.py`.

## Разработка

Проверки (их же гоняет CI на каждый push и pull request):

```bash
pip install -r requirements-dev.txt
ruff check .
mypy --ignore-missing-imports .
pytest
```

`tests/test_smoke.py` - smoke/интеграционная проверка без сети и токена: загрузка и валидация контента, выдача без повторов, авто-цикл, пользовательский контент, аналитика, парсинг конфигурации и регистрация роутеров.

## Лицензия

BSD-3-Clause. Подробности в `LICENSE`.
