import logging
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from constants import (
    CB_DW_BOSS,
    CB_DW_CURSE,
    CB_DW_HOST,
    CB_DW_NEW_GAME,
    CB_DW_PLAYER,
    CB_DW_RESET_BOSSES,
    CB_DW_RESET_CURSES,
    CB_DW_RESET_WORDS,
    CB_DW_ROLES,
    CB_DW_WORD,
    DANGEROUS_WORDS_GAME_ID,
)
from database import DatabaseError, SQLiteHistoryStorage
from handlers.ui import edit_menu, edit_result
from keyboards import (
    create_dangerous_words_host_keyboard,
    create_dangerous_words_player_keyboard,
    create_dangerous_words_role_keyboard,
)
from services.random_generator import (
    Boss,
    Curse,
    DangerousWordsContent,
    EmptyPoolError,
    select_unique_item,
)

logger = logging.getLogger(__name__)


def create_dangerous_words_router(
    content: DangerousWordsContent,
    storage: SQLiteHistoryStorage,
) -> Router:
    """создаёт роутер помощника опасные слова"""
    router = Router()

    @router.callback_query(F.data == CB_DW_ROLES)
    async def handle_dangerous_words_menu(callback: CallbackQuery) -> None:
        """показывает выбор роли в помощнике опасные слова"""
        await edit_menu(
            callback,
            "Играем в “Опасные слова”.\nВыбери роль:",
            create_dangerous_words_role_keyboard(),
        )

    @router.callback_query(F.data == CB_DW_HOST)
    async def handle_dangerous_words_host_menu(
        callback: CallbackQuery,
    ) -> None:
        """показывает меню ведущего"""
        await edit_menu(
            callback,
            "Режим ведущего.\nВыберите действие:",
            create_dangerous_words_host_keyboard(),
        )

    @router.callback_query(F.data == CB_DW_PLAYER)
    async def handle_dangerous_words_player_menu(
        callback: CallbackQuery,
    ) -> None:
        """показывает меню игрока"""
        await edit_menu(
            callback,
            "Режим игрока.\nВыберите действие:",
            create_dangerous_words_player_keyboard(),
        )

    @router.callback_query(F.data == CB_DW_WORD)
    async def handle_create_word(callback: CallbackQuery) -> None:
        """выдаёт новое слово без повтора для пользователя"""
        telegram_id = callback.from_user.id
        try:
            pool = list(
                dict.fromkeys(
                    content.words
                    + await storage.get_custom_words(
                        DANGEROUS_WORDS_GAME_ID
                    )
                )
            )
            word = await select_unique_item(
                items=pool,
                get_item_id=_get_string_id,
                get_seen_ids=storage.get_user_words,
                save_seen_id=storage.save_user_word,
                telegram_id=telegram_id,
            )
            await storage.set_last_word(telegram_id, word)
            word_count = await storage.count_user_words(telegram_id)
        except EmptyPoolError:
            await callback.answer(
                "Список кончился, напомни Сене его пополнить",
                show_alert=True,
            )
            return
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "create_word"},
            )
            await callback.answer(
                "Не удалось сохранить слово. Попробуйте позже.",
                show_alert=True,
            )
            return

        await edit_result(
            callback, f"Новое слово ({word_count}/{len(pool)}):\n\n{word}"
        )

    @router.callback_query(F.data == CB_DW_RESET_WORDS)
    async def handle_reset_words(callback: CallbackQuery) -> None:
        """сбрасывает историю слов пользователя"""
        telegram_id = callback.from_user.id
        try:
            await storage.reset_user_words(telegram_id)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "reset_words"},
            )
            await callback.answer(
                "Не удалось сбросить слова. Попробуйте позже.",
                show_alert=True,
            )
            return

        await callback.answer("Слова сброшены. Счётчик обнулён.", show_alert=True)

    @router.callback_query(F.data == CB_DW_CURSE)
    async def handle_create_curse(callback: CallbackQuery) -> None:
        """выдаёт новое проклятье без повтора в текущем круге"""
        telegram_id = callback.from_user.id
        try:
            pool = content.curses + await storage.get_custom_curses()
            curse, is_new_cycle = await _create_unique_cycle_item(
                items=pool,
                get_item_id=_get_curse_id,
                get_seen_ids=storage.get_user_curses,
                save_seen_id=storage.save_user_curse,
                reset_seen_ids=storage.reset_user_curses,
                telegram_id=telegram_id,
            )
            curse_count = await storage.count_user_curses(telegram_id)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "create_curse"},
            )
            await callback.answer(
                "Не удалось сохранить проклятье. Попробуйте позже.",
                show_alert=True,
            )
            return
        except EmptyPoolError:
            logger.exception(
                "empty_pool_error",
                extra={"telegram_id": telegram_id, "action": "create_curse"},
            )
            await callback.answer(
                "Список проклятий пуст, напомни Сене его пополнить.",
                show_alert=True,
            )
            return

        text = (
            f"Новое проклятье ({curse_count}/{len(pool)}):\n\n"
            f"{curse.title}\n{curse.description}"
        )
        if is_new_cycle:
            text = "Проклятья закончились, начинаем новый круг.\n\n" + text
        await edit_result(callback, text)

    @router.callback_query(F.data == CB_DW_RESET_CURSES)
    async def handle_reset_curses(callback: CallbackQuery) -> None:
        """сбрасывает историю проклятий пользователя"""
        telegram_id = callback.from_user.id
        try:
            await storage.reset_user_curses(telegram_id)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "reset_curses"},
            )
            await callback.answer(
                "Не удалось сбросить проклятья. Попробуйте позже.",
                show_alert=True,
            )
            return

        await callback.answer(
            "Проклятья сброшены. Счётчик обнулён.", show_alert=True
        )

    @router.callback_query(F.data == CB_DW_BOSS)
    async def handle_create_boss(callback: CallbackQuery) -> None:
        """выдаёт нового босса без повтора в текущем круге"""
        telegram_id = callback.from_user.id
        try:
            pool = content.bosses + await storage.get_custom_bosses()
            boss, is_new_cycle = await _create_unique_cycle_item(
                items=pool,
                get_item_id=_get_boss_id,
                get_seen_ids=storage.get_user_bosses,
                save_seen_id=storage.save_user_boss,
                reset_seen_ids=storage.reset_user_bosses,
                telegram_id=telegram_id,
            )
            boss_count = await storage.count_user_bosses(telegram_id)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "create_boss"},
            )
            await callback.answer(
                "Не удалось сохранить босса. Попробуйте позже.",
                show_alert=True,
            )
            return
        except EmptyPoolError:
            logger.exception(
                "empty_pool_error",
                extra={"telegram_id": telegram_id, "action": "create_boss"},
            )
            await callback.answer(
                "Список боссов пуст, напомни Сене его пополнить.",
                show_alert=True,
            )
            return

        text = (
            f"Новый босс ({boss_count}/{len(pool)}):\n\n"
            f"{boss.name}\n{boss.description}"
        )
        if is_new_cycle:
            text = "Боссы закончились, начинаем новый круг.\n\n" + text
        await edit_result(callback, text)

    @router.callback_query(F.data == CB_DW_RESET_BOSSES)
    async def handle_reset_bosses(callback: CallbackQuery) -> None:
        """сбрасывает историю боссов пользователя"""
        telegram_id = callback.from_user.id
        try:
            await storage.reset_user_bosses(telegram_id)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "reset_bosses"},
            )
            await callback.answer(
                "Не удалось сбросить боссов. Попробуйте позже.",
                show_alert=True,
            )
            return

        await callback.answer(
            "Боссы сброшены. Счётчик обнулён.", show_alert=True
        )

    @router.callback_query(F.data == CB_DW_NEW_GAME)
    async def handle_new_game(callback: CallbackQuery) -> None:
        """сбрасывает всю историю выдач пользователя для новой партии"""
        telegram_id = callback.from_user.id
        try:
            await storage.reset_user_all(telegram_id)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "new_game"},
            )
            await callback.answer(
                "Не удалось начать новую игру. Попробуйте позже.",
                show_alert=True,
            )
            return

        await callback.answer(
            "Новая игра: слова, проклятья и боссы сброшены.", show_alert=True
        )

    @router.message(F.text)
    async def handle_unknown_text(message: Message) -> None:
        """отвечает на неизвестный текст"""
        await message.answer("Открой меню командой /start.")

    return router


async def _create_unique_cycle_item[T](
    items: list[T],
    get_item_id: Callable[[T], str],
    get_seen_ids: Callable[[int], Awaitable[set[str]]],
    save_seen_id: Callable[[int, str], Awaitable[None]],
    reset_seen_ids: Callable[[int], Awaitable[None]],
    telegram_id: int,
) -> tuple[T, bool]:
    """выбирает элемент с автоматическим новым кругом после исчерпания"""
    try:
        item = await select_unique_item(
            items=items,
            get_item_id=get_item_id,
            get_seen_ids=get_seen_ids,
            save_seen_id=save_seen_id,
            telegram_id=telegram_id,
        )
    except EmptyPoolError:
        await reset_seen_ids(telegram_id)
        item = await select_unique_item(
            items=items,
            get_item_id=get_item_id,
            get_seen_ids=get_seen_ids,
            save_seen_id=save_seen_id,
            telegram_id=telegram_id,
        )
        return item, True

    return item, False


def _get_string_id(item: str) -> str:
    """возвращает строку как собственный идентификатор"""
    return item


def _get_curse_id(curse: Curse) -> str:
    """возвращает id проклятья"""
    return curse.id


def _get_boss_id(boss: Boss) -> str:
    """возвращает id босса"""
    return boss.id
