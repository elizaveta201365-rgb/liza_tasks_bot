"""
database.py — хранение задач в Supabase.

Простая версия: добавить задачу, посмотреть открытые, отметить выполненной.
Никакого анализа нейросетью — бот просто хранит то, что ты ему написала.

Таблицу в Supabase менять НЕ нужно — используем ту же таблицу `tasks`,
что и раньше. Лишние поля (estimate_minutes, clarifying_question и т.д.)
просто остаются пустыми, они больше не используются.
"""

import os
from datetime import datetime, timezone
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def add_task(chat_id: int, text: str, priority: str | None = None) -> dict:
    """Добавляет задачу сразу как активную (никаких уточнений)."""
    row = {
        "chat_id": chat_id,
        "text": text,
        "status": "active",          # сразу активна
        "priority": priority,        # "high" | "medium" | "low" | None
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = supabase.table("tasks").insert(row).execute()
    return result.data[0]


def get_open_tasks(chat_id: int) -> list[dict]:
    """Все невыполненные задачи."""
    result = (
        supabase.table("tasks")
        .select("*")
        .eq("chat_id", chat_id)
        .neq("status", "done")
        .order("created_at", desc=False)
        .execute()
    )
    return result.data


def mark_task_done(task_id: int) -> dict | None:
    result = (
        supabase.table("tasks")
        .update({
            "status": "done",
            "done_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", task_id)
        .execute()
    )
    return result.data[0] if result.data else None


def get_all_chat_ids() -> list[int]:
    """
    Все chat_id, у кого вообще есть задачи. Нужно для утренней рассылки —
    благодаря этому больше НЕ нужна переменная ALLOWED_CHAT_ID.
    """
    result = supabase.table("tasks").select("chat_id").execute()
    return sorted({row["chat_id"] for row in result.data})
