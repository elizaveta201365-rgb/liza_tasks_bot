"""
ai.py — общение с нейросетью через OpenRouter.

Здесь нейросеть решает: достаточно ли информации по задаче, или нужно
уточнение; сравнивает новую задачу с уже известными похожими; формирует
утреннюю сводку с приоритетами.
"""

import os
import json
import httpx

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Бесплатная модель с хорошим качеством рассуждений.
MODEL = "openrouter/free"


async def ask_ai(system_prompt: str, user_prompt: str) -> str:
    """Отправляет запрос к нейросети и возвращает текст ответа."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


async def ask_ai_json(system_prompt: str, user_prompt: str) -> dict:
    """Как ask_ai, но просит и парсит строго JSON-ответ."""
    text = await ask_ai(
        system_prompt + "\n\nОтвечай ТОЛЬКО валидным JSON, без markdown-разметки и пояснений.",
        user_prompt,
    )
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)


# ---------- Конкретные сценарии ----------

async def analyze_new_task(task_text: str, known_patterns: list[dict]) -> dict:
    """
    Решает: достаточно ли информации по задаче, или нужно уточнение.
    known_patterns — список уже разобранных типовых задач для сравнения по смыслу.

    Возвращает dict вида:
    {
        "needs_clarification": bool,
        "question": str | None,
        "matched_pattern": str | None,
        "priority": "high"|"medium"|"low" | None,
        "estimate_minutes": int | None
    }
    """
    patterns_text = "\n".join(
        f"- «{p['description']}» (оценка: {p['estimate_minutes']} мин, "
        f"{'абстрактная' if p['is_abstract'] else 'понятная'})"
        for p in known_patterns
    ) or "(пока нет сохранённых типовых задач)"

    system_prompt = (
        "Ты — помощник, который помогает человеку разбирать рабочие задачи. "
        "Твоя цель: понять, достаточно ли информации в формулировке задачи, чтобы "
        "оценить её приоритет и время выполнения, или нужно задать один уточняющий вопрос. "
        "Сначала сверь задачу по смыслу со списком уже известных типовых задач — "
        "если она явно похожа на одну из них (даже если слова отличаются), используй "
        "её оценку времени и не задавай вопрос."
    )
    user_prompt = (
        f"Известные типовые задачи пользователя:\n{patterns_text}\n\n"
        f"Новая задача: «{task_text}»\n\n"
        "Ответь в формате JSON со полями: "
        '"needs_clarification" (true/false), '
        '"question" (текст уточняющего вопроса или null), '
        '"matched_pattern" (описание совпавшей типовой задачи или null), '
        '"priority" ("high"/"medium"/"low" или null, если непонятно), '
        '"estimate_minutes" (число или null, если непонятно).'
    )
    return await ask_ai_json(system_prompt, user_prompt)


async def propose_hypothesis(task_text: str, first_answer: str) -> dict:
    """
    Если ответ пользователя на уточняющий вопрос всё ещё расплывчатый,
    нейросеть сама предлагает гипотезу для подтверждения.
    """
    system_prompt = (
        "Ты помогаешь разобрать рабочую задачу. Пользователь ответил на твой уточняющий "
        "вопрос расплывчато. Не переспрашивай снова — вместо этого сам предложи разумную "
        "гипотезу: что именно нужно сделать, и сколько примерно это займёт времени. "
        "Сформулируй это как короткое предложение для подтверждения пользователем."
    )
    user_prompt = (
        f"Задача: «{task_text}»\n"
        f"Ответ пользователя на уточнение: «{first_answer}»\n\n"
        'Ответь в формате JSON: "hypothesis_text" (текст гипотезы для пользователя, '
        'например "Похоже, нужно посмотреть структуру кабинета и сделать список того, '
        'что можно настроить — это займёт час-полтора, верно?"), '
        '"estimate_minutes" (число), "priority" ("high"/"medium"/"low").'
    )
    return await ask_ai_json(system_prompt, user_prompt)


async def build_daily_summary(tasks: list[dict], pending_questions: list[dict]) -> str:
    """Формирует текст утренней рассылки: напоминания + список с приоритетом и рекомендацией."""
    tasks_text = "\n".join(
        f"- [{t.get('priority') or '?'}] «{t['text']}» "
        f"(~{t.get('estimate_minutes') or '?'} мин)"
        for t in tasks
    ) or "(нет открытых задач)"

    system_prompt = (
        "Ты — заботливый и краткий помощник по рабочим задачам. Составь утреннюю сводку "
        "на русском языке: сначала, если есть, напомни про незакрытые уточняющие вопросы. "
        "Затем дай список задач на сегодня, отсортированный по приоритету (важные и срочные — "
        "первыми). В конце выдели ОДНУ задачу, с которой стоит начать, и в 1-2 предложениях "
        "объясни почему (например, 'быстрая и важная — начни с неё' или 'трудоёмкая, лучше "
        "взяться пока есть силы'). Пиши тёпло, по-человечески, без канцелярита."
    )
    user_prompt = f"Открытые задачи:\n{tasks_text}\n\nНезакрытые вопросы:\n" + (
        "\n".join(f"- {q['clarifying_question']} (по задаче «{q['text']}»)" for q in pending_questions)
        or "(нет)"
    )
    return await ask_ai(system_prompt, user_prompt)
