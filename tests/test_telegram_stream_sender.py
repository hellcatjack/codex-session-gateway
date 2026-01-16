import pytest

from src.adapters.telegram_adapter import TelegramStreamSender


class FakeBot:
    def __init__(self) -> None:
        self.sent = []
        self.edits = []
        self._next_id = 1

    async def send_message(self, chat_id: int, text: str):
        self.sent.append((chat_id, text))
        message = type("Msg", (), {"message_id": self._next_id})
        self._next_id += 1
        return message

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        self.edits.append((chat_id, message_id, text))


@pytest.mark.asyncio
async def test_stream_sender_splits_long_text():
    bot = FakeBot()
    sender = TelegramStreamSender(bot, chat_id=1, chunk_limit=4)

    await sender.send("abcdefghij", final=False)

    assert [text for _, text in bot.sent] == ["abcd", "efgh", "ij"]
    assert bot.edits == []


@pytest.mark.asyncio
async def test_stream_sender_appends_then_rolls_over():
    bot = FakeBot()
    sender = TelegramStreamSender(bot, chat_id=1, chunk_limit=8)

    await sender.send("hello", final=False)
    await sender.send("hi", final=False)
    await sender.send("world", final=False)

    assert bot.sent[0][1] == "hello"
    assert bot.sent[1][1] == "world"
    assert bot.edits[-1][2] == "hello\nhi"
