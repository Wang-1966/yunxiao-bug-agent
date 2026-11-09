-- 数据库初始化脚本

-- Bug 表
CREATE TABLE IF NOT EXISTS bugs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bug_id VARCHAR(100) UNIQUE NOT NULL,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    priority VARCHAR(20) NOT NULL, -- critical, important, normal
    status VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending, analyzing, fixing, testing, resolved, failed
    source VARCHAR(50) NOT NULL, -- yunxiao
    source_url TEXT,
    source_status TEXT,
    source_project TEXT,
    source_project_id TEXT,
    repo_path VARCHAR(500),
    branch VARCHAR(100),
    test_command TEXT,
    assigned_agent_id VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

-- 本地补充资料，不受云效同步影响
CREATE TABLE IF NOT EXISTS bug_supplements (
    bug_id TEXT PRIMARY KEY REFERENCES bugs(bug_id),
    notes TEXT NOT NULL DEFAULT '',
    image_names TEXT NOT NULL DEFAULT '[]',
    document TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 云效项目与本地修复仓库绑定
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    branch TEXT NOT NULL,
    test_command TEXT NOT NULL DEFAULT '',
    preview_command TEXT NOT NULL DEFAULT '',
    preview_env_files TEXT NOT NULL DEFAULT '[]',
    preview_port INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Agent 表
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id VARCHAR(100) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'idle', -- idle, busy, paused, error
    current_bug_id VARCHAR(100),
    worktree_path VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (current_bug_id) REFERENCES bugs(bug_id)
);

-- 任务历史表
CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id VARCHAR(100) UNIQUE NOT NULL,
    bug_id VARCHAR(100) NOT NULL,
    agent_id VARCHAR(100) NOT NULL,
    stage VARCHAR(50) NOT NULL, -- analyze, propose, fix, verify, complete
    status VARCHAR(20) NOT NULL, -- running, completed, failed
    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP,
    duration INTEGER, -- 秒
    llm_provider VARCHAR(50),
    llm_model VARCHAR(100),
    tokens_used INTEGER DEFAULT 0,
    cost DECIMAL(10, 6) DEFAULT 0,
    result TEXT,
    error TEXT,
    FOREIGN KEY (bug_id) REFERENCES bugs(bug_id),
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
);

-- Agent 日志表
CREATE TABLE IF NOT EXISTS agent_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id VARCHAR(100) NOT NULL,
    bug_id VARCHAR(100),
    log_level VARCHAR(20) NOT NULL, -- debug, info, warning, error
    message TEXT NOT NULL,
    metadata TEXT, -- JSON 格式的额外信息
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id),
    FOREIGN KEY (bug_id) REFERENCES bugs(bug_id)
);

-- LLM 配置表
CREATE TABLE IF NOT EXISTS llm_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider VARCHAR(50) NOT NULL, -- claude, grok, openai, etc.
    enabled BOOLEAN NOT NULL DEFAULT 1,
    base_url VARCHAR(500) NOT NULL,
    api_key VARCHAR(500) NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    stage VARCHAR(50), -- analyze, propose, fix, verify, 或 NULL 表示默认
    max_tokens INTEGER DEFAULT 4000,
    temperature DECIMAL(3, 2) DEFAULT 0.7,
    timeout INTEGER DEFAULT 60,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 决策记录表
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id VARCHAR(100) UNIQUE NOT NULL,
    bug_id VARCHAR(100) NOT NULL,
    agent_id VARCHAR(100) NOT NULL,
    stage VARCHAR(50) NOT NULL,
    question TEXT NOT NULL,
    options TEXT NOT NULL, -- JSON 数组
    user_choice TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending, answered, timeout
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    answered_at TIMESTAMP,
    FOREIGN KEY (bug_id) REFERENCES bugs(bug_id),
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
);

-- 创建索引
-- 对话独立于运行日志，按 Bug 聚合，保留不同修复轮次。
CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bug_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text TEXT NOT NULL,
    images TEXT NOT NULL DEFAULT '[]',
    document TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    client_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(agent_id, client_id)
);
CREATE INDEX IF NOT EXISTS idx_conversation_bug ON conversation_messages(bug_id, id);
CREATE INDEX IF NOT EXISTS idx_conversation_pending ON conversation_messages(agent_id, status, id);

CREATE INDEX IF NOT EXISTS idx_bugs_status ON bugs(status);
CREATE INDEX IF NOT EXISTS idx_bugs_priority ON bugs(priority);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_task_history_bug_id ON task_history(bug_id);
CREATE INDEX IF NOT EXISTS idx_task_history_agent_id ON task_history(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_logs_agent_id ON agent_logs(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_logs_bug_id ON agent_logs(bug_id);
CREATE INDEX IF NOT EXISTS idx_llm_config_provider ON llm_config(provider);
CREATE INDEX IF NOT EXISTS idx_llm_config_stage ON llm_config(stage);
CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status);
