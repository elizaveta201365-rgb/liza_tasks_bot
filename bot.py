"""
bot.py — простой телеграм-бот для рабочих задач.

Логика двух «команд» (по первому слову сообщения):
- Сообщение начинается со слова «задача» → бот ДОБАВЛЯЕТ задачу.
  Приоритет можно сказать прямо в задаче словом «высокий», «средний»
  или «низкий» (можно с «приоритет»). Не сказала — задача без приоритета.
- Сообщение начинается со слова «сделала» (или «выполнила», «готово»,
  «закрыла») → бот ЗАКРЫВАЕТ подходящую задачу, находя её по словам.
- Любое другое сообщение бот за задачу НЕ принимает, а мягко подсказывает,
  как добавить или закрыть.

Приоритеты в списке: 🔴 высокий, 🟡 средний, 🟣 низкий, ⚪️ без приоритета.
Команда /list — показать список. Вт–пт в 11:00 — автоматическая рассылка.
"""

import os
import re
import logging
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Moscow")

PRIORITY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟣", None: "⚪️"}
PRIORITY_LABEL = {
    "high": "Высокий приоритет",
    "medium": "Средний приоритет",
    "low": "Низкий приоритет",
    None: "Без приоритета",
}
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2, None: 3}

# Команда «добавить»: сообщение начинается с этого слова.
ADD_PREFIX = re.compile(r"^\s*задач\w*\s*[:,\-—]?\s*", re.IGNORECASE)

# Команда «закрыть»: прошедшее время «сделала/выполнила/готово/закрыла».
# Важно: «сделать»/«закрыть» (что НУЖНО сделать) сюда не попадают.
DONE_STEMS = ("сделал", "сделан", "выполнил", "выполнен", "готов", "закрыл", "закрыт")
DONE_PREFIX = re.compile(r"^\s*(сделал\w*|выполнил\w*|готов\w*|закрыл\w*)\b", re.IGNORECASE)

# Слова, которые не помогают опознать задачу при поиске по словам.
STOPWORDS = {"задача", "задачу", "это", "уже", "там", "для", "под", "над", "про"}


# ---------- Приоритет ----------

def extract_priority(text: str):
    """Достаёт приоритет и убирает слова про приоритет из текста задачи."""
    lowered = text.lower()
    priority = None
    if re.search(r"высок", lowered):
        priority = "high"
    elif re.search(r"средн", lowered):
        priority = "medium"
    elif re.search(r"низк", lowered):
        priority = "low"
    clean = re.sub(
        r"\s*(высок\w*|средн\w*|низк\w*)\s*(приоритет\w*)?",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    clean = re.sub(r"\s+", " ", clean).strip(" .,-—:")
    return priority, (clean or text.strip())


# ---------- Поиск задачи для закрытия ----------

def significant_words(text: str) -> list[str]:
    words = re.findall(r"\w+", text.lower())
    return [
        w for w in words
        if len(w) >= 3 and w not in STOPWORDS and not w.startswith(DONE_STEMS)
    ]


def word_matches(a: str, b: str) -> bool:
    """Считает слова совпавшими, прощая русские окончания
    (проанализировать ↔ проанализировала, сайт ↔ сайта)."""
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if long.startswith(short) and len(short) >= 4:
        return True
    common = 0
    for x, y in zip(a, b):
        if x == y:
            common += 1
        else:
            break
    return common >= 5


def find_done_task(message: str, open_tasks: list[dict]) -> dict | None:
    msg_words = significant_words(message)
    if not msg_words:
        return None
    best, best_score = None, 0
    for t in open_tasks:
        task_words = significant_words(t["text"])
        score = sum(
            1 for tw in task_words if any(word_matches(tw, mw) for mw in msg_words)
        )
        if score > best_score:
            best, best_score = t, score
    return best if best_score >= 1 else None


# ---------- Формат списка ----------

def format_task_list(tasks: list[dict]) -> str:
    tasks_sorted = sorted(tasks, key=lambda t: PRIORITY_ORDER.get(t.get("priority"), 3))
    blocks, block_lines, current = [], [], object()
    counter = 1
    for t in tasks_sorted:
        priority = t.get("priority")
        if priority != current:
            if block_lines:
                blocks.append("\n".join(block_lines))
            current = priority
            emoji = PRIORITY_EMOJI.get(priority, "⚪️")
            block_lines = [f"{emoji} *{PRIORITY_LABEL.get(priority, 'Без приоритета')}*"]
        block_lines.append(f"{counter}. {t['text']}")
        counter += 1
    if block_lines:
        blocks.append("\n".join(block_lines))
    return "\n\n".join(blocks)


# ---------- Команды ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я храню твои рабочие задачи.\n\n"
        "• Добавить — начни сообщение со слова «задача»:\n"
        "  «задача проанализировать конкурентов высокий приоритет».\n"
        "• Приоритет — слово высокий / средний / низкий внутри задачи "
        "(не скажешь — запишу без приоритета).\n"
        "• Закрыть — начни со слова «сделала» и пару слов из задачи:\n"
        "  «сделала проанализировала конкурентов».\n"
        "• /list — показать все задачи.\n\n"
        "Каждый будний день (вт–пт) в 11:00 пришлю список на день 🙂"
    )


async def list_tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tasks = db.get_open_tasks(chat_id)
    if not tasks:
        await update.message.reply_text("Сейчас открытых задач нет 🎉")
        return
    await update.message.reply_text(format_task_list(tasks), parse_mode="Markdown")


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /check — прислать утреннюю рассылку прямо сейчас (для проверки)."""
    await daily_job(context.application)


# ---------- Обработка сообщений ----------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    log.info("Сообщение от chat_id=%s: %s", chat_id, text)
    try:
        await _handle(update, chat_id, text)
    except Exception:
        log.exception("Ошибка при обработке сообщения от chat_id=%s", chat_id)
        await update.message.reply_text("Что-то пошло не так — попробуй ещё раз 🙏")


async def _handle(update: Update, chat_id: int, text: str):
    # 1) «задача ...» — добавить (проверяем первым, это команда).
    if ADD_PREFIX.match(text):
        rest = ADD_PREFIX.sub("", text, count=1).strip()
        if not rest:
            await update.message.reply_text("Напиши после слова «задача» саму задачу 🙂")
            return
        priority, clean = extract_priority(rest)
        db.add_task(chat_id, clean, priority)
        emoji = PRIORITY_EMOJI[priority]
        label = PRIORITY_LABEL[priority].lower()
        await update.message.reply_text(
            f"Записала задачу 👍 Приоритет: {label} {emoji}\n«{clean}»"
        )
        return

    # 2) «сделала ...» — закрыть.
    if DONE_PREFIX.match(text):
        open_tasks = db.get_open_tasks(chat_id)
        if not open_tasks:
            await update.message.reply_text("Сейчас в списке нет открытых задач 🤷")
            return
        match = find_done_task(text, open_tasks)
        if match:
            db.mark_task_done(match["id"])
            await update.message.reply_text(f"Готово, закрыла «{match['text']}» ✅")
        else:
            await update.message.reply_text(
                "Не поняла, какую задачу закрыть. Напиши «сделала ...» "
                "и пару слов из самой задачи."
            )
        return

    # 3) Всё остальное за задачу НЕ принимаем — подсказываем.
    await update.message.reply_text(
        "Чтобы добавить задачу — начни сообщение со слова «задача».\n"
        "Чтобы закрыть — со слова «сделала» и пары слов из задачи.\n"
        "Список задач — команда /list."
    )


# ---------- Утренняя рассылка ----------

async def daily_job(application):
    for chat_id in db.get_all_chat_ids():
        try:
            tasks = db.get_open_tasks(chat_id)
            if not tasks:
                continue
            text = "Доброе утро! Вот задачи на сегодня:\n\n" + format_task_list(tasks)
            await application.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        except Exception:
            log.exception("Не удалось отправить сводку для chat_id=%s", chat_id)


# ---------- Точка входа ----------

def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", list_tasks_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    scheduler = AsyncIOScheduler(timezone=ZoneInfo(TIMEZONE))
    scheduler.add_job(
        lambda: application.create_task(daily_job(application)),
        CronTrigger(day_of_week="tue-fri", hour=11, minute=0),
    )
    scheduler.start()

    application.run_polling()


if __name__ == "__main__":
    main()
