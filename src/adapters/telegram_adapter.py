from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..commands import CommandType, parse_command
from ..config import Config
from ..codex_runner import CodexRunner
from ..orchestrator import Orchestrator


TELEGRAM_MESSAGE_LIMIT = 4096
_DEDUP_TTL_SECONDS = 3600
_DEDUP_MAX_ENTRIES = 256
# If we recently streamed output for this user (via /new or free-form text),
# suppress JSONL-sync "final" messages that are already present in the stream buffer.
# This avoids confusing "late" duplicates arriving after "运行完成/等待新指令" status updates.
_STREAM_BUFFER_DEDUP_WINDOW_SECONDS = 10 * 60
# Bound memory growth for long-running streams; we only need the tail for dedupe.
_STREAM_BUFFER_MAX_CHARS = 200_000


@dataclass
class _UserContext:
    last_prompt: Optional[str] = None
    chat_id: Optional[int] = None
    stream_buffer: str = ""
    # Same as stream_buffer but without injecting separators between chunks.
    # This is important for dedupe: StreamBroker may split long final messages
    # mid-token, and stream_buffer will contain extra newlines we added.
    stream_buffer_compact: str = ""
    stream_buffer_updated_at: float = 0.0
    dedupe_hashes: dict[str, float] = field(default_factory=dict)
    pending_jsonl: list[str] = field(default_factory=list)
    jsonl_sender: Optional["TelegramStreamSender"] = None
    jsonl_sender_chat_id: Optional[int] = None


class TelegramStreamSender:
    def __init__(self, bot, chat_id: int, chunk_limit: int) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._chunk_limit = max(1, min(chunk_limit, TELEGRAM_MESSAGE_LIMIT))
        self._message_id: Optional[int] = None
        self._full_text = ""

    async def send(self, text: str, final: bool) -> None:
        if not text:
            return
        if self._full_text:
            next_text = f"{self._full_text}\n{text}"
        else:
            next_text = text

        if len(next_text) > self._chunk_limit:
            await self._send_new_message(text)
            return

        self._full_text = next_text
        if self._message_id is None:
            await self._send_new_message(self._full_text)
            return

        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id,
                message_id=self._message_id,
                text=self._full_text,
            )
        except BadRequest:
            await self._send_new_message(self._full_text)

    async def _send_new_message(self, text: str) -> None:
        for chunk in self._split_text(text):
            message = await self._bot.send_message(chat_id=self._chat_id, text=chunk)
            self._message_id = message.message_id
            self._full_text = chunk

    def _split_text(self, text: str) -> list[str]:
        if len(text) <= self._chunk_limit:
            return [text]
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + self._chunk_limit, len(text))
            chunks.append(text[start:end])
            start = end
        return chunks


class TelegramAdapter:
    def __init__(self, config: Config, orchestrator: Orchestrator, bot_id: str = "default") -> None:
        self._config = config
        self._orchestrator = orchestrator
        self._bot_id = bot_id
        self._user_context: dict[int, _UserContext] = {}
        self._logger = logging.getLogger(__name__)

    def _hash_text(self, text: str) -> str | None:
        if not text:
            return None
        normalized = CodexRunner.normalize_text_for_dedupe(text)
        if not normalized:
            return None
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _reset_dedupe(self, user_id: int) -> _UserContext:
        ctx = self._user_context.setdefault(user_id, _UserContext())
        ctx.stream_buffer = ""
        ctx.stream_buffer_compact = ""
        ctx.stream_buffer_updated_at = 0.0
        ctx.dedupe_hashes.clear()
        ctx.pending_jsonl.clear()
        # New conversation must start a fresh Telegram message bubble.
        ctx.jsonl_sender = None
        ctx.jsonl_sender_chat_id = None
        return ctx

    def _append_stream_buffer(self, ctx: _UserContext, text: str) -> None:
        if not text:
            return
        ctx.stream_buffer_compact = f"{ctx.stream_buffer_compact}{text}"
        if len(ctx.stream_buffer_compact) > _STREAM_BUFFER_MAX_CHARS:
            ctx.stream_buffer_compact = ctx.stream_buffer_compact[-_STREAM_BUFFER_MAX_CHARS :]
        if ctx.stream_buffer:
            ctx.stream_buffer = f"{ctx.stream_buffer}\n{text}"
        else:
            ctx.stream_buffer = text
        if len(ctx.stream_buffer) > _STREAM_BUFFER_MAX_CHARS:
            ctx.stream_buffer = ctx.stream_buffer[-_STREAM_BUFFER_MAX_CHARS :]
        ctx.stream_buffer_updated_at = time.time()

    def _prune_dedupe(self, ctx: _UserContext) -> None:
        if not ctx.dedupe_hashes:
            return
        now = time.time()
        expired = [key for key, ts in ctx.dedupe_hashes.items() if now - ts > _DEDUP_TTL_SECONDS]
        for key in expired:
            ctx.dedupe_hashes.pop(key, None)
        if len(ctx.dedupe_hashes) <= _DEDUP_MAX_ENTRIES:
            return
        for key, _ in sorted(ctx.dedupe_hashes.items(), key=lambda item: item[1])[: len(ctx.dedupe_hashes) - _DEDUP_MAX_ENTRIES]:
            ctx.dedupe_hashes.pop(key, None)

    def _should_send(self, ctx: _UserContext, text: str) -> bool:
        # If this JSONL message is already visible in the stream output we sent recently,
        # skip it to prevent late duplicates (JSONL sync runs on an interval).
        if (ctx.stream_buffer or ctx.stream_buffer_compact) and ctx.stream_buffer_updated_at:
            now = time.time()
            if now - ctx.stream_buffer_updated_at <= _STREAM_BUFFER_DEDUP_WINDOW_SECONDS:
                candidate = CodexRunner.normalize_text_for_dedupe(text)
                if candidate:
                    streamed = CodexRunner.normalize_text_for_dedupe(ctx.stream_buffer)
                    if candidate in streamed:
                        return False
                    compact = CodexRunner.normalize_text_for_dedupe(ctx.stream_buffer_compact)
                    # Handles long messages split mid-token by stream chunking.
                    if candidate in compact:
                        return False
                    # Handles cases where the same content was streamed line-by-line (no newlines in compact buffer).
                    candidate_flat = candidate.replace("\n", "")
                    if len(candidate_flat) >= 64:
                        compact_flat = compact.replace("\n", "")
                        if candidate_flat and candidate_flat in compact_flat:
                            return False

        digest = self._hash_text(text)
        if digest is None:
            return True
        self._prune_dedupe(ctx)
        if digest in ctx.dedupe_hashes:
            return False
        ctx.dedupe_hashes[digest] = time.time()
        return True

    def _record_stream_digest(self, ctx: _UserContext) -> None:
        digest = self._hash_text(ctx.stream_buffer)
        if digest is None:
            return
        self._prune_dedupe(ctx)
        ctx.dedupe_hashes[digest] = time.time()

    def _get_jsonl_sender(
        self, user_ctx: _UserContext, bot, chat_id: int
    ) -> TelegramStreamSender:
        sender = user_ctx.jsonl_sender
        if sender is not None and user_ctx.jsonl_sender_chat_id == chat_id:
            return sender
        sender = TelegramStreamSender(bot, chat_id, self._config.message_chunk_limit)
        user_ctx.jsonl_sender = sender
        user_ctx.jsonl_sender_chat_id = chat_id
        return sender

    def run(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        application = ApplicationBuilder().token(self._config.telegram_bot_token).build()
        application.post_init = self._post_init
        application.add_handler(CommandHandler("help", self._handle_help))
        application.add_handler(CommandHandler("whoami", self._handle_whoami))
        application.add_handler(CommandHandler("session", self._handle_session))
        application.add_handler(CommandHandler("stop", self._handle_stop))
        application.add_handler(CommandHandler("status", self._handle_status))
        application.add_handler(CommandHandler("retry", self._handle_retry))
        application.add_handler(CommandHandler("new", self._handle_new))
        application.add_handler(CommandHandler("lastresult", self._handle_lastresult))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
        )
        self._logger.info("Telegram 适配器启动，进入 polling bot_id=%s", self._bot_id)
        application.run_polling(close_loop=False, stop_signals=None)

    async def _post_init(self, application: Application) -> None:
        if self._config.jsonl_sync_interval_seconds <= 0:
            return
        if application.job_queue is not None:
            application.job_queue.run_repeating(
                self._sync_jsonl_tick,
                interval=self._config.jsonl_sync_interval_seconds,
                first=1.0,
            )
            return
        self._logger.warning("未启用 JobQueue，改用内置轮询同步 JSONL")
        application.create_task(self._sync_jsonl_loop(application))

    async def _handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                "可用命令：\n"
                "/new <内容> 新建会话并提交指令\n"
                "/session 查看当前会话绑定（只读）\n"
                "/stop 停止当前任务\n"
                "/status 查看状态\n"
                "/retry 重试上一次指令\n"
                "/lastresult 查看最近一次结果\n"
                "/whoami 查看用户 ID\n"
                "/help 查看帮助"
            ),
        )

    async def _handle_whoami(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update, context):
            return
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        await context.bot.send_message(
            chat_id=chat_id, text=f"user_id={user_id}, chat_id={chat_id}"
        )

    async def _handle_session(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update, context):
            return
        text = update.message.text
        command = parse_command(text)
        payload = command.payload if command else None
        if not payload:
            await self._orchestrator.status(
                update.effective_user.id,
                lambda msg: context.bot.send_message(
                    chat_id=update.effective_chat.id, text=msg
                ),
            )
            return
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="会话绑定已禁用，当前仅支持查看状态。",
        )
        return

    async def _handle_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        await self._orchestrator.cancel_run(
            update.effective_user.id,
            lambda msg: context.bot.send_message(chat_id=update.effective_chat.id, text=msg),
        )

    async def _handle_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update, context):
            return
        await self._orchestrator.status(
            update.effective_user.id,
            lambda msg: context.bot.send_message(chat_id=update.effective_chat.id, text=msg),
        )

    async def _handle_lastresult(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update, context):
            return
        sender = TelegramStreamSender(
            context.bot, update.effective_chat.id, self._config.message_chunk_limit
        )
        await self._orchestrator.last_result(
            update.effective_user.id,
            lambda msg: context.bot.send_message(chat_id=update.effective_chat.id, text=msg),
            sender.send,
        )

    async def _handle_retry(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        user_id = update.effective_user.id
        last_prompt = self._user_context.get(user_id, _UserContext()).last_prompt
        await self._orchestrator.retry_last(
            user_id,
            last_prompt,
            lambda msg: context.bot.send_message(chat_id=update.effective_chat.id, text=msg),
            TelegramStreamSender(
                context.bot, update.effective_chat.id, self._config.message_chunk_limit
            ).send,
        )

    async def _handle_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        text = update.message.text
        command = parse_command(text)
        payload = command.payload if command else None
        if not payload:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text="请提供指令内容。"
            )
            return
        # Mirror Codex CLI behavior: `/new xxx` starts a new session.
        await self._submit_prompt(update, context, f"/new {payload}")

    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorized(update, context):
            return
        await self._submit_prompt(update, context, update.message.text)

    async def _submit_prompt(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str
    ) -> None:
        user_id = update.effective_user.id
        self._logger.info("收到消息 user_id=%s bot_id=%s", user_id, self._bot_id)
        user_ctx = self._reset_dedupe(user_id)
        user_ctx.last_prompt = prompt
        sender = TelegramStreamSender(
            context.bot, update.effective_chat.id, self._config.message_chunk_limit
        )
        async def send_stream(text: str, final: bool) -> None:
            if text:
                self._append_stream_buffer(user_ctx, text)
            await sender.send(text, final)
            if final:
                self._record_stream_digest(user_ctx)

        await self._orchestrator.submit_prompt(
            user_id,
            prompt,
            lambda msg: context.bot.send_message(chat_id=update.effective_chat.id, text=msg),
            send_stream,
        )

    async def _authorized(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        user_id = update.effective_user.id
        if not self._config.telegram_allowed_user_ids:
            self._logger.warning("未配置允许用户列表 user_id=%s", user_id)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="未配置允许的用户列表，请联系管理员。",
            )
            return False
        if user_id not in self._config.telegram_allowed_user_ids:
            self._logger.warning("拒绝用户 user_id=%s", user_id)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="无权限使用此机器人。",
            )
            return False
        chat_id = update.effective_chat.id
        self._user_context.setdefault(user_id, _UserContext()).chat_id = chat_id
        await self._orchestrator.set_chat_id(user_id, chat_id)
        return True

    async def _sync_jsonl_tick(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_ids = (
            self._config.telegram_allowed_user_ids
            if self._config.telegram_allowed_user_ids
            else set(self._user_context.keys())
        )
        for user_id in list(user_ids):
            user_ctx = self._user_context.setdefault(user_id, _UserContext())
            if user_ctx.chat_id is None:
                user_ctx.chat_id = self._orchestrator.get_last_chat_id(user_id)
            if user_ctx.chat_id is None:
                continue
            try:
                running = await self._orchestrator.is_running(user_id)
                messages = await self._orchestrator.poll_external_results(
                    user_id, allow_send=not running
                )
            except Exception as exc:
                self._logger.warning("JSONL 同步失败 user_id=%s err=%s", user_id, exc)
                continue
            progress_messages = [msg for msg in messages if getattr(msg, "is_progress", False)]
            final_messages = [msg for msg in messages if not getattr(msg, "is_progress", False)]

            def extract_text(msg: object) -> str:
                return msg.text if hasattr(msg, "text") else str(msg)

            progress_texts = [extract_text(msg) for msg in progress_messages]
            final_texts = [extract_text(msg) for msg in final_messages]
            if running and progress_texts:
                if not self._config.jsonl_stream_events:
                    sender = self._get_jsonl_sender(
                        user_ctx, context.bot, user_ctx.chat_id
                    )
                    for message in progress_texts:
                        if not self._should_send(user_ctx, message):
                            self._logger.info(
                                "JSONL 去重：跳过重复进度 user_id=%s bot_id=%s",
                                user_id,
                                self._bot_id,
                            )
                            continue
                        await sender.send(message, True)
            if running:
                if final_texts:
                    user_ctx.pending_jsonl.extend(final_texts)
                continue

            pending = user_ctx.pending_jsonl
            if not final_texts and not pending:
                continue
            user_ctx.pending_jsonl = []
            sender = self._get_jsonl_sender(user_ctx, context.bot, user_ctx.chat_id)
            for message in pending + final_texts:
                if not self._should_send(user_ctx, message):
                    self._logger.info("JSONL 去重：跳过重复结果 user_id=%s bot_id=%s", user_id, self._bot_id)
                    continue
                # Keep JSONL sync output in one editable message to avoid repeated notifications.
                await sender.send(message, True)
            # A completed result batch marks the end of one conversation output.
            # Freeze current bubble so subsequent batches always open a fresh one.
            user_ctx.jsonl_sender = None
            user_ctx.jsonl_sender_chat_id = None

    async def _sync_jsonl_loop(self, application: Application) -> None:
        try:
            while True:
                await self._sync_jsonl_tick(
                    ContextTypes.DEFAULT_TYPE(application=application)
                )
                await asyncio.sleep(self._config.jsonl_sync_interval_seconds)
        except asyncio.CancelledError:
            return
