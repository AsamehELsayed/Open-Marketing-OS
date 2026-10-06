-- Open Marketing OS v0.2 — authoritative schema. Single source of DDL.
PRAGMA user_version = 10;

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  website TEXT NOT NULL DEFAULT '',
  goal TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  settings_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'starter' REFERENCES projects(id),
  kind TEXT NOT NULL DEFAULT 'learning',
  body_md TEXT NOT NULL DEFAULT '',
  confidence TEXT NOT NULL DEFAULT 'MEDIUM',
  source_ref TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id);

CREATE TABLE IF NOT EXISTS companies (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  website TEXT,
  markets_json TEXT NOT NULL DEFAULT '{}',
  languages_json TEXT NOT NULL DEFAULT '[]',
  services_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id),
  project_id TEXT NOT NULL DEFAULT 'starter',
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  archived_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  role TEXT NOT NULL,
  body_md TEXT NOT NULL DEFAULT '',
  citations_json TEXT NOT NULL DEFAULT '[]',
  client_message_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  project_id TEXT NOT NULL DEFAULT 'starter',
  client_message_id TEXT NOT NULL DEFAULT '',
  user_message_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'running',
  provider TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_client_msg ON turns(conversation_id, client_message_id);

CREATE TABLE IF NOT EXISTS execution_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL DEFAULT 'starter',
  conversation_id TEXT NOT NULL DEFAULT '',
  turn_id TEXT NOT NULL DEFAULT '',
  job_id TEXT NOT NULL DEFAULT '',
  event_type TEXT NOT NULL DEFAULT '',
  label TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exec_turn ON execution_events(turn_id, id);
CREATE INDEX IF NOT EXISTS idx_exec_job ON execution_events(job_id, id);
CREATE INDEX IF NOT EXISTS idx_exec_convo ON execution_events(conversation_id, id);

CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'starter',
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'proposed',
  impact INTEGER NOT NULL DEFAULT 0,
  confidence INTEGER NOT NULL DEFAULT 0,
  effort INTEGER NOT NULL DEFAULT 0,
  cost INTEGER NOT NULL DEFAULT 0,
  approval_level TEXT NOT NULL DEFAULT 'Green',
  measurement_window TEXT,
  result TEXT,
  learning_ref TEXT,
  workflow_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'starter',
  campaign_id TEXT REFERENCES campaigns(id),
  title TEXT NOT NULL,
  lane TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  acceptance TEXT NOT NULL DEFAULT '',
  job_id TEXT,
  due_at TEXT,
  workflow_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'
);

CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'starter',
  kind TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  body_md TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  fields_json TEXT NOT NULL DEFAULT '{}',
  decided_by TEXT,
  decided_at TEXT,
  updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'
);

CREATE TABLE IF NOT EXISTS prospects (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'starter',
  campaign_id TEXT REFERENCES campaigns(id),
  name TEXT NOT NULL,
  evidence_url TEXT,
  status TEXT NOT NULL DEFAULT 'listed',
  notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'starter',
  campaign_id TEXT REFERENCES campaigns(id),
  hypothesis TEXT NOT NULL DEFAULT '',
  metric TEXT NOT NULL DEFAULT '',
  window_days INTEGER NOT NULL DEFAULT 0,
  start_date TEXT,
  next_review TEXT,
  status TEXT NOT NULL DEFAULT 'drafted',
  stop_condition TEXT NOT NULL DEFAULT '',
  workflow_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'
);

CREATE TABLE IF NOT EXISTS measurements (
  id TEXT PRIMARY KEY,
  experiment_id TEXT NOT NULL REFERENCES experiments(id),
  observed_at TEXT NOT NULL,
  evidence_md TEXT NOT NULL DEFAULT '',
  decision TEXT NOT NULL DEFAULT 'none'
);

CREATE TABLE IF NOT EXISTS learnings (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'starter',
  experiment_id TEXT REFERENCES experiments(id),
  body_md TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  ref_id TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS background_jobs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'starter',
  conversation_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT '',
  job_type TEXT NOT NULL DEFAULT '',
  brief_md TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'queued',
  cmd_json TEXT NOT NULL DEFAULT '[]',
  cwd TEXT NOT NULL DEFAULT '',
  exit_code INTEGER,
  log_path TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00',
  updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'
);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'starter',
  path TEXT NOT NULL UNIQUE,
  source_kind TEXT NOT NULL DEFAULT 'legacy_unknown',
  source_ref TEXT NOT NULL DEFAULT '',
  expected_chunk_count INTEGER,
  file_sha TEXT NOT NULL DEFAULT '',
  status_tag TEXT NOT NULL DEFAULT 'UNKNOWN',
  indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'starter',
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_id TEXT NOT NULL,
  header TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL DEFAULT '',
  token_est INTEGER NOT NULL DEFAULT 0,
  UNIQUE (document_id, chunk_id)
);

-- Keyword index (mandatory, always on). Content mirrors chunks(text).
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, header, path, tokenize = 'porter'
);

-- DEV-003 C1: project-scoped social identity (6+ platforms, VERIFIED/LIKELY policy).
CREATE TABLE IF NOT EXISTS social_accounts (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT 'starter',
  platform TEXT NOT NULL,
  handle TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'LIKELY',
  source TEXT NOT NULL DEFAULT 'manual',
  evidence_url TEXT NOT NULL DEFAULT '',
  observed_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (project_id, platform)
);
CREATE INDEX IF NOT EXISTS idx_social_project ON social_accounts(project_id);

-- DEV-005 W5: model-call telemetry. Additive only; token/cost columns are
-- nullable so unknown provider usage stays NULL (never 0-filled). Local legs
-- record estimated_cost_usd = 0.00 with cost_note = compute-not-metered marker.
CREATE TABLE IF NOT EXISTS model_calls (
  call_id TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL DEFAULT '',
  project_id TEXT NOT NULL DEFAULT 'starter',
  provider TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  adapter TEXT NOT NULL DEFAULT '',
  quantization TEXT NOT NULL DEFAULT '',
  route_mode TEXT NOT NULL DEFAULT 'AUTO',
  route_reason TEXT NOT NULL DEFAULT '',
  input_tokens INTEGER,
  cached_tokens INTEGER,
  output_tokens INTEGER,
  reasoning_tokens INTEGER,
  total_tokens INTEGER,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  estimated_cost_usd REAL,
  pricing_version TEXT NOT NULL DEFAULT '',
  cost_note TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL DEFAULT '',
  ended_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_model_calls_turn ON model_calls(turn_id);
CREATE INDEX IF NOT EXISTS idx_model_calls_project ON model_calls(project_id);

-- DEV-007 W2: project_files metadata table (additive only).
CREATE TABLE IF NOT EXISTS project_files (
  file_id        TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL,
  original_name  TEXT NOT NULL,
  safe_name      TEXT NOT NULL,
  mime_detected  TEXT NOT NULL,
  size           INTEGER NOT NULL,
  sha256         TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('document','image','data','other')),
  width          INTEGER,
  height         INTEGER,
  extraction     TEXT NOT NULL DEFAULT 'pending'
                   CHECK (extraction IN ('pending','ready','stripped','failed')),
  attach_scope   TEXT NOT NULL DEFAULT 'project'
                   CHECK (attach_scope IN ('turn','project')),
  indexed        INTEGER NOT NULL DEFAULT 0,
  index_status   TEXT NOT NULL DEFAULT 'pending'
                   CHECK (index_status IN ('pending','indexed','failed','quarantined','not_searchable')),
  index_error    TEXT NOT NULL DEFAULT '',
  rel_path       TEXT NOT NULL,
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_files_project
  ON project_files(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_project_files_sha
  ON project_files(project_id, sha256);

-- DEV-015 W3: bind temporary files to one conversation and record only the
-- selected IDs on each turn. The runtime repository mirrors this DDL.
CREATE TABLE IF NOT EXISTS turn_file_bindings (
  file_id TEXT PRIMARY KEY REFERENCES project_files(file_id) ON DELETE CASCADE,
  project_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turn_file_binding_conversation
  ON turn_file_bindings(project_id, conversation_id);

CREATE TABLE IF NOT EXISTS turn_attachment_selections (
  turn_id TEXT NOT NULL,
  file_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  PRIMARY KEY (turn_id, file_id)
);

-- DEV-007 W4: tool run telemetry.
CREATE TABLE IF NOT EXISTS tool_runs (
  tool_run_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  tool_id TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL DEFAULT '',
  completed_at TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '',
  latency_ms INTEGER NOT NULL DEFAULT 0,
  cost_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tool_runs_project ON tool_runs(project_id);
CREATE INDEX IF NOT EXISTS idx_tool_runs_tool ON tool_runs(tool_id, started_at);

-- DEV-007 W5: Integration Registry + credential vault refs.
CREATE TABLE IF NOT EXISTS integrations (
  integration_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  provider TEXT NOT NULL,
  scope TEXT NOT NULL,
  status TEXT NOT NULL,
  project_id TEXT NOT NULL DEFAULT '',
  user_id TEXT NOT NULL DEFAULT '',
  config_json TEXT NOT NULL DEFAULT '{}',
  secret_ref TEXT,
  last_health TEXT,
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (project_id, scope, integration_id, capability)
);
CREATE INDEX IF NOT EXISTS idx_integrations_project ON integrations(project_id);
CREATE INDEX IF NOT EXISTS idx_integrations_capability ON integrations(capability, project_id);

CREATE TABLE IF NOT EXISTS credentials_refs (
  secret_ref TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  label TEXT NOT NULL,
  project_id TEXT NOT NULL DEFAULT '',
  user_id TEXT NOT NULL DEFAULT '',
  backend TEXT NOT NULL DEFAULT 'dpapi_file',
  backend_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT '',
  revoked_at TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_credentials_refs_active
  ON credentials_refs(scope, label, project_id, user_id)
  WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_credentials_refs_scope
  ON credentials_refs(scope, label);

-- DEV-007 W6: MCP Gateway tables.
CREATE TABLE IF NOT EXISTS mcp_servers (
  server_id TEXT PRIMARY KEY,
  endpoint TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT '',
  secret_ref TEXT NOT NULL DEFAULT '',
  connected INTEGER NOT NULL DEFAULT 0,
  last_health TEXT NOT NULL DEFAULT 'unknown',
  last_checked_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS mcp_tools_allowlist (
  server_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  granted_by TEXT NOT NULL DEFAULT '',
  granted_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (server_id, project_id, tool_name),
  FOREIGN KEY (server_id) REFERENCES mcp_servers(server_id),
  FOREIGN KEY (project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_mcp_servers_scope ON mcp_servers(scope);
CREATE INDEX IF NOT EXISTS idx_mcp_allowlist_project ON mcp_tools_allowlist(project_id);
CREATE INDEX IF NOT EXISTS idx_mcp_allowlist_server ON mcp_tools_allowlist(server_id);
