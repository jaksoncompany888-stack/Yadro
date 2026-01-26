"""
Yadro v0 - Database Schema

Core tables:
- users: пользователи
- tasks: задачи
- task_events: audit log
- task_steps: план и шаги
- schedules: расписания
- memory_items: память
- costs: токены и стоимость
"""

SCHEMA_SQL = """
-- Users table
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER UNIQUE NOT NULL,
    username TEXT,
    is_active INTEGER DEFAULT 1,
    settings TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users(tg_id);

-- Tasks table (state machine)
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    task_type TEXT DEFAULT 'general',
    input_text TEXT,
    input_data TEXT DEFAULT '{}',
    
    -- State machine
    status TEXT DEFAULT 'created' CHECK(status IN ('created', 'queued', 'running', 'paused', 'succeeded', 'failed', 'cancelled')),
    pause_reason TEXT CHECK(pause_reason IS NULL OR pause_reason IN ('approval', 'dependency', 'rate_limit')),
    
    -- Retry logic
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    
    -- Locking
    locked_by TEXT,
    locked_at TEXT,
    lease_expires_at TEXT,
    
    -- Plan tracking
    current_plan_id TEXT,
    current_step_id TEXT,
    
    -- Results
    result TEXT,
    error TEXT,
    
    -- Timestamps
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_status_lease ON tasks(status, lease_expires_at);

-- Task events (audit log)
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    event_type TEXT NOT NULL,
    event_data TEXT DEFAULT '{}',
    step_id TEXT,
    tool_name TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events(task_id);

-- Task steps (plan execution)
CREATE TABLE IF NOT EXISTS task_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    plan_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    action TEXT NOT NULL,
    action_data TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending',
    result TEXT,
    error TEXT,
    snapshot_ref TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    
    UNIQUE(task_id, plan_id, step_id)
);

CREATE INDEX IF NOT EXISTS idx_task_steps_task_plan ON task_steps(task_id, plan_id);

-- Schedules table
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    task_spec TEXT NOT NULL,
    run_at TEXT,
    cron TEXT,
    next_run_at TEXT,
    last_run_at TEXT,
    run_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'executed', 'cancelled', 'paused')),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_schedules_user_id ON schedules(user_id);
CREATE INDEX IF NOT EXISTS idx_schedules_next_run ON schedules(status, next_run_at);

-- Memory items
CREATE TABLE IF NOT EXISTS memory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    memory_type TEXT NOT NULL CHECK(memory_type IN ('fact', 'decision', 'context', 'task', 'feedback')),
    content TEXT NOT NULL,
    source_task_id INTEGER REFERENCES tasks(id),
    importance REAL DEFAULT 0.5,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    accessed_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_items_user ON memory_items(user_id);
CREATE INDEX IF NOT EXISTS idx_memory_items_type ON memory_items(memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_items_importance ON memory_items(importance);

-- FTS for memory search
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    content,
    content_rowid=id,
    tokenize='porter unicode61'
);

-- Costs tracking
CREATE TABLE IF NOT EXISTS costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    task_id INTEGER REFERENCES tasks(id),
    operation TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_costs_user ON costs(user_id);
CREATE INDEX IF NOT EXISTS idx_costs_task ON costs(task_id);

-- Drafts (SMM черновики)
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    text TEXT NOT NULL,
    topic TEXT,
    channel_id TEXT,
    publish_at TEXT,
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft', 'scheduled', 'published', 'error')),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_drafts_user_id ON drafts(user_id);
CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status, publish_at);

-- View for tasks with user info
CREATE VIEW IF NOT EXISTS tasks_with_user AS
SELECT 
    t.*,
    u.tg_id,
    u.username
FROM tasks t
JOIN users u ON t.user_id = u.id;
"""


def init_schema(connection) -> None:
    """Initialize database schema."""
    connection.executescript(SCHEMA_SQL)
    connection.commit()
