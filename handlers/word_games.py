import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from constants import (
    CB_WG_OPEN_PREFIX,
    CB_WG_RESET_PREFIX,
    CB_WG_WORD_PREFIX,
)
from database import DatabaseError, SQLiteHistoryStorage
from handlers.ui import edit_menu, edit_result
from keyboards import create_word_game_keyboard
from services.random_generator import (
    EmptyPoolError,
    WordGame,
    select_unique_item,
)

logger = logging.getLogger(__name__)


def create_word_games_router(
    word_games: list[WordGame],
    storage: SQLiteHistoryStorage,
) -> Router:
    """создаёт роутер словесных игр (крокодил, алиас и т.п.)"""
    router = Router()
    games_by_id = {game.game_id: game for game in word_games}

    @router.callback_query(_data_prefix(CB_WG_OPEN_PREFIX))
    async def handle_open_game(callback: CallbackQuery) -> None:
        """открывает меню словесной игры"""
        game = _resolve_game(callback.data, CB_WG_OPEN_PREFIX, games_by_id)
        if game is None:
            await callback.answer()
            return

        await edit_menu(
            callback,
            f"«{game.title}».\nЖми кнопку, чтобы получить слово.",
            create_word_game_keyboard(game.game_id),
        )

    @router.callback_query(_data_prefix(CB_WG_WORD_PREFIX))
    async def handle_get_word(callback: CallbackQuery) -> None:
        """выдаёт слово словесной игры без повтора с авто-кругом"""
        game = _resolve_game(callback.data, CB_WG_WORD_PREFIX, games_by_id)
        if game is None:
            await callback.answer()
            return

        telegram_id = callback.from_user.id
        try:
            pool = list(
                dict.fromkeys(
                    game.words
                    + await storage.get_custom_words(game.game_id)
                )
            )
            if await storage.get_user_auto_cycle(telegram_id):
                word, is_new_cycle = await _select_word_with_cycle(
                    pool, storage, telegram_id, game.game_id
                )
            else:
                word = await _select_word_once(
                    pool, storage, telegram_id, game.game_id
                )
                is_new_cycle = False
            await storage.set_last_word(telegram_id, word)
            count = await storage.count_user_game_words(
                telegram_id, game.game_id
            )
        except EmptyPoolError:
            await callback.answer(
                "Слова кончились. Жми «Новая игра».", show_alert=True
            )
            return
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "wg_word"},
            )
            await callback.answer(
                "Не удалось выдать слово. Попробуйте позже.", show_alert=True
            )
            return

        text = f"Слово ({count}/{len(pool)}):\n\n{word}"
        if is_new_cycle:
            text = "Слова закончились, начинаем новый круг.\n\n" + text
        await edit_result(callback, text)

    @router.callback_query(_data_prefix(CB_WG_RESET_PREFIX))
    async def handle_reset_game(callback: CallbackQuery) -> None:
        """сбрасывает историю слов словесной игры"""
        game = _resolve_game(callback.data, CB_WG_RESET_PREFIX, games_by_id)
        if game is None:
            await callback.answer()
            return

        telegram_id = callback.from_user.id
        try:
            await storage.reset_user_game_words(telegram_id, game.game_id)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "wg_reset"},
            )
            await callback.answer(
                "Не удалось сбросить. Попробуйте позже.", show_alert=True
            )
            return

        await callback.answer("Новая игра: слова сброшены.", show_alert=True)

    return router


def _data_prefix(prefix: str):
    """создаёт фильтр callback по префиксу данных"""
    return lambda callback: (
        callback.data is not None and callback.data.startswith(prefix)
    )


def _resolve_game(
    data: str | None, prefix: str, games_by_id: dict[str, WordGame]
) -> WordGame | None:
    """извлекает игру по game_id из callback-данных"""
    if data is None:
        return None
    return games_by_id.get(data[len(prefix) :])


async def _select_word_once(
    pool: list[str],
    storage: SQLiteHistoryStorage,
    telegram_id: int,
    game_id: str,
) -> str:
    """выбирает слово без повтора, бросает EmptyPoolError при исчерпании"""

    async def get_seen(user_id: int) -> set[str]:
        return await storage.get_user_game_words(user_id, game_id)

    async def save_seen(user_id: int, word: str) -> None:
        await storage.save_user_game_word(user_id, game_id, word)

    return await select_unique_item(
        items=pool,
        get_item_id=_get_string_id,
        get_seen_ids=get_seen,
        save_seen_id=save_seen,
        telegram_id=telegram_id,
    )


async def _select_word_with_cycle(
    pool: list[str],
    storage: SQLiteHistoryStorage,
    telegram_id: int,
    game_id: str,
) -> tuple[str, bool]:
    """выбирает слово с авто-сбросом круга после исчерпания"""
    try:
        return await _select_word_once(
            pool, storage, telegram_id, game_id
        ), False
    except EmptyPoolError:
        await storage.reset_user_game_words(telegram_id, game_id)
        return await _select_word_once(
            pool, storage, telegram_id, game_id
        ), True


def _get_string_id(item: str) -> str:
    """возвращает строку как собственный идентификатор"""
    return item
