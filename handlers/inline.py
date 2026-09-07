import random

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from constants import DANGEROUS_WORDS_GAME_ID
from database import SQLiteHistoryStorage
from services.content import DangerousWordsContent

INLINE_RESULTS_LIMIT = 10


def create_inline_router(
    content: DangerousWordsContent, storage: SQLiteHistoryStorage
) -> Router:
    """создаёт роутер инлайн-режима выдачи слов с поиском по запросу"""
    router = Router()

    @router.inline_query()
    async def handle_inline_query(query: InlineQuery) -> None:
        """выдаёт слова по запросу, пустой запрос отдаёт случайную выборку

        answer помечен type: ignore: список однотипных статей корректен в
        рантайме, но mypy ругается на инвариантность list против union-типа
        """
        custom_words = await storage.get_custom_words(DANGEROUS_WORDS_GAME_ID)
        pool = list(dict.fromkeys(content.words + custom_words))
        words = _select_inline_words(pool, query.query.strip())
        results = [
            InlineQueryResultArticle(
                id=str(index),
                title=word,
                input_message_content=InputTextMessageContent(message_text=word),
            )
            for index, word in enumerate(words)
        ]
        await query.answer(results, cache_time=0, is_personal=True)  # type: ignore[arg-type]

    return router


def _select_inline_words(pool: list[str], query_text: str) -> list[str]:
    """отбирает слова пула по подстроке запроса без учёта регистра"""
    if query_text == "":
        return random.sample(pool, k=min(INLINE_RESULTS_LIMIT, len(pool)))
    lowered = query_text.lower()
    matched = [word for word in pool if lowered in word.lower()]
    return matched[:INLINE_RESULTS_LIMIT]
