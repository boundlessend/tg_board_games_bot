import random

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from services.random_generator import DangerousWordsContent

INLINE_RESULTS_LIMIT = 10


def create_inline_router(content: DangerousWordsContent) -> Router:
    """создаёт роутер инлайн-режима выдачи случайных слов"""
    router = Router()

    @router.inline_query()
    async def handle_inline_query(query: InlineQuery) -> None:
        """выдаёт случайные слова в любом чате без учёта истории"""
        sample_size = min(INLINE_RESULTS_LIMIT, len(content.words))
        words = random.sample(content.words, k=sample_size)
        results = [
            InlineQueryResultArticle(
                id=str(index),
                title=word,
                input_message_content=InputTextMessageContent(
                    message_text=word
                ),
            )
            for index, word in enumerate(words)
        ]
        # ponytail: список однотипных статей корректен в рантайме,
        # mypy ругается на инвариантность list против union-типа answer
        await query.answer(results, cache_time=0, is_personal=True)  # type: ignore[arg-type]

    return router
