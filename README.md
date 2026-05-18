# tg_board_games_bot

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

В `.env` укажите токен:

```env
BOT_TOKEN=your_real_telegram_bot_token
```

## Запуск

```bash
python bot.py
```

При первом запуске рядом с кодом создаётся `bot.sqlite3`.

## Данные

- `data/words.json` — 500 обычных и 500 фэнтези-слов.
- `data/curses.json` — 100 проклятий.
- `data/bosses.json` — 100 боссов.

В сообщениях показывается счётчик выдачи: слова до `1000`, проклятья и боссы до `100`. Слова заканчиваются после 1000 уникальных выдач пользователю. Проклятья и боссы после исчерпания начинают новый круг.

В меню «Опасные слова» есть два режима:

- `Я ведуший` — слова, проклятья, боссы и отдельные сбросы каждого списка.
- `Я игрок` — слова и сброс слов.

## Админ-меню

Напишите боту `abobus` в личном чате (Изменить секретную фразу можно в ADMIN_SECRET_PHRASE в файле constants.py). Бот сразу покажет статистику по всем Telegram ID. Кнопка `Вся статистика` повторно выводит тот же отчёт.

## Добавление игр

1. Добавьте кнопку игры в `keyboards.py`.
2. Создайте роутер в `handlers/`.
3. Подключите роутер в `bot.py`.
4. Положите данные игры в `data/`.
5. Общую выдачу без повторов можно переиспользовать из `services/random_generator.py`.

## Лицензия

MIT. Подробности в `LICENSE`.
