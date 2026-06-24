"""
bot.py — основная логика телеграм-бота для управления рабочими задачами.

Сценарий работы:
- Пользователь пишет задачу обычным текстом.
- Бот через нейросеть решает: достаточно ли информации, или нужно уточнение.
- Если нужно уточнение — задаёт один вопрос. Если ответ всё равно расплывчатый —
  предлагает свою гипотезу для подтверждения.
- Каждый будний день в 11:00 (по часовому поясу пользователя, см. TIMEZONE)
  бот сам присылает сводку: сначала напоминания про незакрытые вопросы,
  потом список задач с приоритетом и рекомендацией, с чего начать.
- Пользователь пишет "сделала <текст>" чтобы отметить задачу выполненной.
"""

import os
import logging
from datetime import time
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import database as db
import ai

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Moscow")

# Простая "память в рамках сессии": какой задаче сейчас задан уточняющий вопрос,
# чтобы понять, что следующее сообщение пользователя — это ответ на него.
# Формат: {chat_id: task_id}
AWAITING_ANSWER: dict[int, int] = {}
# Если уже была одна попытка уточнения и мы предложили гипотезу — ждём да/нет.
AWAITING_HYPOTHESIS_CONFIRM: dict[int, int] = {}


# ---------- Команды ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я помогу разбирать рабочие задачи и каждое утро буду присылать "
        "список с приоритетами.\n\n"
        "Просто пиши мне задачи текстом — я сам разберусь, понятно сформулировано "
        "или нужно уточнить детали.\n\n"
        "Когда задача сделана — напиши, например: «сделала отчёт по Avito»."
    )


async def list_tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tasks = db.get_open_tasks(chat_id)
    if not tasks:
        await update.message.reply_text("Сейчас открытых задач нет 🎉")
        return
    lines = [f"{i+1}. {t['text']} [{t.get('priority') or 'без приоритета'}]" for i, t in enumerate(tasks)]
    await update.message.reply_text("\n".join(lines))


# ---------- Обработка обычных сообщений ----------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # Случай 1: ждём подтверждения гипотезы (да/нет на предложенный вариант).
    if chat_id in AWAITING_HYPOTHESIS_CONFIRM:
        task_id = AWAITING_HYPOTHESIS_CONFIRM.pop(chat_id)
        task = db.get_task_by_id(task_id)
        db.update_task(
            task_id,
            status="active",
            clarifying_question=None,
        )
        await update.message.reply_text("Принято, добавила в список с этой оценкой 👍")
        return

    # Случай 2: ждём ответа на уточняющий вопрос.
    if chat_id in AWAITING_ANSWER:
        task_id = AWAITING_ANSWER.pop(chat_id)
        task = db.get_task_by_id(task_id)
        hypothesis = await ai.propose_hypothesis(task["text"], text)
        db.update_task(
            task_id,
            priority=hypothesis["priority"],
            estimate_minutes=hypothesis["estimate_minutes"],
        )
        AWAITING_HYPOTHESIS_CONFIRM[chat_id] = task_id
        await update.message.reply_text(hypothesis["hypothesis_text"])
        return

    # Случай 3: отметка "сделала ...".
    lowered = text.lower()
    if lowered.startswith("сделал") or lowered.startswith("готово") or lowered.startswith("выполнил"):
        open_tasks = db.get_open_tasks(chat_id)
        # Простое сопоставление: ищем задачу, чей текст частично совпадает с сообщением.
        match = None
        for t in open_tasks:
            if t["text"].lower() in lowered or any(word in lowered for word in t["text"].lower().split()[:3]):
                match = t
                break
        if match:
            db.mark_task_done(match["id"])
            await update.message.reply_text(f"Отлично, отметила «{match['text']}» как выполненную ✅")
        else:
            await update.message.reply_text(
                "Не нашла подходящую задачу в списке. Уточни, пожалуйста, какую именно задачу закрыть?"
            )
        return

    # Случай 4: новая задача.
    patterns = db.get_known_task_patterns(chat_id)
    analysis = await ai.analyze_new_task(text, patterns)

    task = db.add_task(chat_id, text)

    if analysis.get("needs_clarification"):
        question = analysis["question"]
        db.update_task(task["id"], clarifying_question=question)
        AWAITING_ANSWER[chat_id] = task["id"]
        await update.message.reply_text(question)
    else:
        db.update_task(
            task["id"],
            status="active",
            priority=analysis.get("priority"),
            estimate_minutes=analysis.get("estimate_minutes"),
        )
        # Если нашли совпадение с типовой задачей — сохраним для обучения базы.
        if not analysis.get("matched_pattern"):
            db.add_known_task_pattern(
                chat_id,
                text,
                analysis.get("estimate_minutes") or 0,
                is_abstract=False,
            )
        await update.message.reply_text("Добавила задачу в список 👍")


# ---------- Утренняя рассылка ----------

async def send_daily_summary(application, chat_id: int):
    pending = db.get_tasks_needing_review(chat_id)
    open_tasks = [t for t in db.get_open_tasks(chat_id) if t["status"] == "active"]
    summary = await ai.build_daily_summary(open_tasks, pending)
    await application.bot.send_message(chat_id=chat_id, text=summary)


async def daily_job(application, chat_ids: list[int]):
    for chat_id in chat_ids:
        try:
            await send_daily_summary(application, chat_id)
        except Exception:
            log.exception("Не удалось отправить сводку для chat_id=%s", chat_id)


# ---------- Точка входа ----------

def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", list_tasks_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Список ваших chat_id для рассылки. Сейчас бот один на один с вами —
    # chat_id определится автоматически после первого /start, его можно
    # прописать через переменную окружения ALLOWED_CHAT_ID на Railway.
    allowed_chat_id = os.environ.get("ALLOWED_CHAT_ID")
    chat_ids = [int(allowed_chat_id)] if allowed_chat_id else []

    scheduler = AsyncIOScheduler(timezone=ZoneInfo(TIMEZONE))
    # Понедельник-пятница, в 11:00. Если нужно вторник-пятница — поменять day_of_week.
    scheduler.add_job(
        lambda: application.create_task(daily_job(application, chat_ids)),
        CronTrigger(day_of_week="tue-fri", hour=11, minute=0),
    )
    scheduler.start()

    application.run_polling()


if __name__ == "__main__":
    main()
