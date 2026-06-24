-- Выполнить этот скрипт в Supabase: SQL Editor -> New query -> вставить -> Run

create table tasks (
    id bigint generated always as identity primary key,
    chat_id bigint not null,
    text text not null,
    source text default '',
    status text not null default 'needs_review',  -- needs_review | active | done
    priority text,                                  -- high | medium | low
    estimate_minutes int,
    actual_minutes int,
    clarifying_question text,
    created_at timestamptz not null default now(),
    done_at timestamptz
);

create table known_task_patterns (
    id bigint generated always as identity primary key,
    chat_id bigint not null,
    description text not null,
    estimate_minutes int,
    is_abstract boolean default false,
    created_at timestamptz not null default now()
);

create index idx_tasks_chat_id on tasks (chat_id);
create index idx_patterns_chat_id on known_task_patterns (chat_id);
