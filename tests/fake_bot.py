"""фейковый бот: перехватывает вызовы telegram api без сети

нужен, чтобы прогонять хендлеры целиком через Dispatcher.feed_update и
проверять, что именно бот отправил в чат и в личку
"""

from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberMember,
    Message,
    User,
)

BOT_ID = 999_000
BOT_USERNAME = "board_games_test_bot"
CHAT_ADMIN_ID = 4242


class RecordingSession(BaseSession):
    """сессия, которая запоминает запросы и отвечает правдоподобно"""

    def __init__(self) -> None:
        """создаёт сессию с пустым журналом запросов"""
        super().__init__()
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.blocked_users: set[int] = set()
        self._next_message_id = 1000

    def sent_to(self, chat_id: int) -> list[str]:
        """возвращает тексты сообщений, отправленных в указанный чат"""
        return [
            str(payload.get("text", ""))
            for name, payload in self.calls
            if name in {"SendMessage", "EditMessageText"}
            and payload.get("chat_id") == chat_id
        ]

    def method_names(self) -> list[str]:
        """возвращает имена вызванных методов в порядке вызова"""
        return [name for name, _ in self.calls]

    def alerts(self) -> list[str]:
        """возвращает тексты всплывающих ответов на callback"""
        return [
            str(payload.get("text", ""))
            for name, payload in self.calls
            if name == "AnswerCallbackQuery"
        ]

    async def close(self) -> None:
        """закрывает сессию (ничего не держит)"""

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,
    ) -> TelegramType:
        """записывает вызов и отдаёт минимально достаточный ответ"""
        name = type(method).__name__
        payload = method.model_dump(exclude_none=True)
        self.calls.append((name, payload))

        if name == "SendMessage" and payload.get("chat_id") in self.blocked_users:
            raise TelegramForbiddenError(
                method=method, message="bot was blocked by the user"
            )
        if name == "GetMe":
            return _bot_user()  # type: ignore[return-value]
        if name in {"SendMessage", "EditMessageText", "SendDocument"}:
            return self._message(payload)  # type: ignore[return-value]
        if name == "GetChatMember":
            return _chat_member(int(payload["user_id"]))  # type: ignore[return-value]
        return True  # type: ignore[return-value]

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        """заглушка потокового чтения"""
        yield b""

    def _message(self, payload: dict[str, Any]) -> Message:
        """строит ответное сообщение на отправку или правку"""
        self._next_message_id += 1
        chat_id = int(payload.get("chat_id", 0))
        return Message.model_construct(
            message_id=int(payload.get("message_id", self._next_message_id)),
            date=datetime(2026, 1, 1),
            chat=Chat.model_construct(
                id=chat_id, type="private" if chat_id > 0 else "supergroup"
            ),
            text=str(payload.get("text", "")),
        )


def make_bot(session: RecordingSession) -> Bot:
    """создаёт бота поверх записывающей сессии"""
    return Bot(token=f"{BOT_ID}:test-token-value-placeholder", session=session)


def _bot_user() -> User:
    """описание самого бота для getMe"""
    return User.model_construct(
        id=BOT_ID, is_bot=True, first_name="TestBot", username=BOT_USERNAME
    )


def _chat_member(user_id: int) -> ChatMemberAdministrator | ChatMemberMember:
    """возвращает участника чата: администратор только CHAT_ADMIN_ID"""
    user = User.model_construct(id=user_id, is_bot=False, first_name="U")
    if user_id == CHAT_ADMIN_ID:
        return ChatMemberAdministrator.model_construct(
            status="administrator",
            user=user,
            can_be_edited=False,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=True,
            can_manage_video_chats=True,
            can_restrict_members=True,
            can_promote_members=False,
            can_change_info=True,
            can_invite_users=True,
        )
    return ChatMemberMember.model_construct(status="member", user=user)
