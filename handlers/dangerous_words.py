import logging
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.types import Message

from constants import (
    BACK_TO_DANGEROUS_WORDS_TITLE,
    BOSSES_LIMIT,
    CREATE_BOSS_TITLE,
    CREATE_CURSE_TITLE,
    CREATE_WORD_TITLE,
    CURSES_LIMIT,
    DANGEROUS_WORDS_GAME_TITLE,
    DANGEROUS_WORDS_HOST_TITLE,
    DANGEROUS_WORDS_PLAYER_TITLE,
    RESET_BOSSES_TITLE,
    RESET_CURSES_TITLE,
    RESET_WORDS_TITLE,
    WORDS_LIMIT,
)
from database import DatabaseError, SQLiteHistoryStorage
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

    @router.message(F.text == DANGEROUS_WORDS_GAME_TITLE)
    async def handle_dangerous_words_menu(message: Message) -> None:
        """показывает меню помощника опасные слова"""
        await _answer_dangerous_words_role_menu(message)

    @router.message(F.text == BACK_TO_DANGEROUS_WORDS_TITLE)
    async def handle_back_to_dangerous_words_menu(message: Message) -> None:
        """возвращает к выбору роли в помощнике опасные слова"""
        await _answer_dangerous_words_role_menu(message)

    @router.message(F.text == DANGEROUS_WORDS_HOST_TITLE)
    async def handle_dangerous_words_host_menu(message: Message) -> None:
        """показывает меню ведущего"""
        await message.answer(
            "Режим ведущего.\nВыберите действие:",
            reply_markup=create_dangerous_words_host_keyboard(),
        )

    @router.message(F.text == DANGEROUS_WORDS_PLAYER_TITLE)
    async def handle_dangerous_words_player_menu(message: Message) -> None:
        """показывает меню игрока"""
        await message.answer(
            "Режим игрока.\nВыберите действие:",
            reply_markup=create_dangerous_words_player_keyboard(),
        )

    @router.message(F.text == CREATE_WORD_TITLE)
    async def handle_create_word(message: Message) -> None:
        """выдаёт новое слово без повтора для пользователя"""
        telegram_id = _extract_telegram_id(message)
        try:
            word = await select_unique_item(
                items=content.words,
                get_item_id=_get_string_id,
                get_seen_ids=storage.get_user_words,
                save_seen_id=storage.save_user_word,
                telegram_id=telegram_id,
            )
            word_count = await storage.count_user_words(telegram_id)
        except EmptyPoolError:
            await message.answer("Список кончился, напомни Сене его пополнить")
            return
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "create_word"},
            )
            await message.answer(
                "Не удалось сохранить слово. Попробуйте позже."
            )
            return

        await message.answer(
            f"Новое слово ({word_count}/{WORDS_LIMIT}):\n\n{word}"
        )

    @router.message(F.text == RESET_WORDS_TITLE)
    async def handle_reset_words(message: Message) -> None:
        """сбрасывает историю слов пользователя"""
        telegram_id = _extract_telegram_id(message)
        try:
            await storage.reset_user_words(telegram_id)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "reset_words"},
            )
            await message.answer(
                "Не удалось сбросить слова. Попробуйте позже."
            )
            return

        await message.answer(f"Слова сброшены. Счётчик снова 0/{WORDS_LIMIT}.")

    @router.message(F.text == CREATE_CURSE_TITLE)
    async def handle_create_curse(message: Message) -> None:
        """выдаёт новое проклятье без повтора в текущем круге"""
        telegram_id = _extract_telegram_id(message)
        try:
            curse, is_new_cycle = await _create_unique_cycle_item(
                items=content.curses,
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
            await message.answer(
                "Не удалось сохранить проклятье. Попробуйте позже."
            )
            return
        except EmptyPoolError:
            logger.exception(
                "empty_pool_error",
                extra={"telegram_id": telegram_id, "action": "create_curse"},
            )
            await message.answer(
                "Список проклятий пуст, напомни Сене его пополнить."
            )
            return

        if is_new_cycle:
            await message.answer(
                "Список проклятий кончился, напомни Сене его пополнить."
            )
        await message.answer(
            f"Новое проклятье ({curse_count}/{CURSES_LIMIT}):\n\n{curse.title}\n{curse.description}"
        )

    @router.message(F.text == RESET_CURSES_TITLE)
    async def handle_reset_curses(message: Message) -> None:
        """сбрасывает историю проклятий пользователя"""
        telegram_id = _extract_telegram_id(message)
        try:
            await storage.reset_user_curses(telegram_id)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "reset_curses"},
            )
            await message.answer(
                "Не удалось сбросить проклятья. Попробуйте позже."
            )
            return

        await message.answer(
            f"Проклятья сброшены. Счётчик снова 0/{CURSES_LIMIT}."
        )

    @router.message(F.text == CREATE_BOSS_TITLE)
    async def handle_create_boss(message: Message) -> None:
        """выдаёт нового босса без повтора в текущем круге"""
        telegram_id = _extract_telegram_id(message)
        try:
            boss, is_new_cycle = await _create_unique_cycle_item(
                items=content.bosses,
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
            await message.answer(
                "Не удалось сохранить босса. Попробуйте позже."
            )
            return
        except EmptyPoolError:
            logger.exception(
                "empty_pool_error",
                extra={"telegram_id": telegram_id, "action": "create_boss"},
            )
            await message.answer(
                "Список боссов пуст, напомни Сене его пополнить."
            )
            return

        if is_new_cycle:
            await message.answer(
                "Список боссов кончился, напомни Сене его пополнить."
            )
        await message.answer(
            f"Новый босс ({boss_count}/{BOSSES_LIMIT}):\n\n{boss.name}\n{boss.description}"
        )

    @router.message(F.text == RESET_BOSSES_TITLE)
    async def handle_reset_bosses(message: Message) -> None:
        """сбрасывает историю боссов пользователя"""
        telegram_id = _extract_telegram_id(message)
        try:
            await storage.reset_user_bosses(telegram_id)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": telegram_id, "action": "reset_bosses"},
            )
            await message.answer(
                "Не удалось сбросить боссов. Попробуйте позже."
            )
            return

        await message.answer(
            f"Боссы сброшены. Счётчик снова 0/{BOSSES_LIMIT}."
        )

    @router.message(F.text)
    async def handle_unknown_text(message: Message) -> None:
        """отвечает на неизвестный текст"""
        await message.answer("Выберите действие с помощью кнопок меню.")

    return router


async def _answer_dangerous_words_role_menu(message: Message) -> None:
    """показывает выбор роли помощника опасные слова"""
    await message.answer(
        "Играем в “Опасные слова”.\nВыбери роль:",
        reply_markup=create_dangerous_words_role_keyboard(),
    )


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


def _extract_telegram_id(message: Message) -> int:
    """возвращает telegram id отправителя сообщения"""
    if message.from_user is None:
        raise DatabaseError("Не удалось определить telegram_id пользователя.")
    return message.from_user.id


def _get_string_id(item: str) -> str:
    """возвращает строку как собственный идентификатор"""
    return item


def _get_curse_id(curse: Curse) -> str:
    """возвращает id проклятья"""
    return curse.id


def _get_boss_id(boss: Boss) -> str:
    """возвращает id босса"""
    return boss.id
