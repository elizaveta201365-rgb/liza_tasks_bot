"""
database.py — хранение задач в Supabase.

Здесь живёт вся работа со списком задач: добавить, посмотреть, отметить
выполненной, найти похожую задачу в "базе знаний" типовых задач.
"""

import os
from datetime import datetime, timezone
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ---------- Задачи ----------

def add_task(chat_id: int, text: str, source: str = "") -> dict:
    """Добавляет новую задачу со статусом 'нужно разобрать'."""
    row = {
        "chat_id": chat_id,
        "text": text,
        "source": source,
        "status": "needs_review",   # needs_review -> active -> done
        "priority": None,
        "estimate_minutes": None,
        "clarifying_question": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = supabase.table("tasks").insert(row).execute()
    return result.data[0]


def get_open_tasks(chat_id: int) -> list[dict]:
    """Все задачи, которые ещё не выполнены (active или needs_review)."""
    result = (
        supabase.table("tasks")
        .select("*")
        .eq("chat_id", chat_id)
        .neq("status", "done")
        .order("created_at", desc=False)
        .execute()
    )
    return result.data


def get_tasks_needing_review(chat_id: int) -> list[dict]:
    result = (
        supabase.table("tasks")
        .select("*")
        .eq("chat_id", chat_id)
        .eq("status", "needs_review")
        .execute()
    )
    return result.data


def update_task(task_id: int, **fields) -> dict:
    result = supabase.table("tasks").update(fields).eq("id", task_id).execute()
    return result.data[0] if result.data else None


def mark_task_done(task_id: int, actual_minutes: int | None = None) -> dict:
    fields = {
        "status": "done",
        "done_at": datetime.now(timezone.utc).isoformat(),
    }
    if actual_minutes is not None:
        fields["actual_minutes"] = actual_minutes
    return update_task(task_id, **fields)


def get_task_by_id(task_id: int) -> dict | None:
    result = supabase.table("tasks").select("*").eq("id", task_id).execute()
    return result.data[0] if result.data else None


# ---------- База типовых задач (для смыслового сравнения) ----------

def add_known_task_pattern(chat_id: int, description: str, estimate_minutes: int, is_abstract: bool) -> dict:
    """
    Сохраняет 'типовую задачу' — например, 'замена фото на сайте' — чтобы
    в будущем нейросеть могла сверяться с этим списком по смыслу.
    """
    row = {
        "chat_id": chat_id,
        "description": description,
        "estimate_minutes": estimate_minutes,
        "is_abstract": is_abstract,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = supabase.table("known_task_patterns").insert(row).execute()
    return result.data[0]


def get_known_task_patterns(chat_id: int) -> list[dict]:
    result = (
        supabase.table("known_task_patterns")
        .select("*")
        .eq("chat_id", chat_id)
        .execute()
    )
    return result.data
