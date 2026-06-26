import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database import DatabaseError, SQLiteHistoryStorage

logger = logging.getLogger(__name__)


def create_favorites_router(storage: SQLiteHistoryStorage) -> Router:
    """создаёт роутер избранного: /fav, /favorites, /favclear"""
    router = Router()

    @router.message(Command("fav"))
    async def handle_fav(message: Message) -> None:
        """добавляет последнее выданное слово в избранное"""
        user = message.from_user
        if user is None:
            return

        try:
            last_word = await storage.get_last_word(user.id)
            if last_word is None:
                await message.answer("Сначала получи слово, потом /fav.")
                return
            added = await storage.add_favorite(user.id, last_word)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": user.id, "action": "fav"},
            )
            await message.answer("Не удалось добавить в избранное.")
            return

        if added:
            await message.answer(f"Добавлено в избранное: {last_word}")
        else:
            await message.answer(f"Уже в избранном: {last_word}")

    @router.message(Command("favorites"))
    async def handle_favorites(message: Message) -> None:
        """показывает избранное пользователя"""
        user = message.from_user
        if user is None:
            return

        try:
            favorites = await storage.get_favorites(user.id)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": user.id, "action": "favorites"},
            )
            await message.answer("Не удалось получить избранное.")
            return

        if len(favorites) == 0:
            await message.answer("Избранное пусто. Жми /fav после слова.")
            return

        await message.answer("Избранное:\n" + "\n".join(favorites))

    @router.message(Command("favclear"))
    async def handle_favclear(message: Message) -> None:
        """очищает избранное пользователя"""
        user = message.from_user
        if user is None:
            return

        try:
            await storage.clear_favorites(user.id)
        except DatabaseError:
            logger.exception(
                "database_error",
                extra={"telegram_id": user.id, "action": "favclear"},
            )
            await message.answer("Не удалось очистить избранное.")
            return

        await message.answer("Избранное очищено.")

    return router
