export interface Bug {
  bug_id: string;
  title: string;
  description?: string;
  description_format?: string;
  detail_warning?: string;
  media_warning?: string;
  media_urls?: Record<string, string>;
  attachments?: { id: string; fileId?: string; fileName: string; suffix?: string; url?: string }[];
  priority: 'critical' | 'important' | 'normal';
  status: 'pending' | 'analyzing' | 'fixing' | 'testing' | 'awaiting_plan_confirmation' | 'awaiting_confirmation' | 'resolved' | 'failed';
  source: string;
  source_url?: string;
  source_status?: string;
  source_project?: string;
  source_project_id?: string;
  test_command?: string;
  repo_path?: string;
  branch?: string;
  assigned_agent_id?: string;
  created_at?: string;
  updated_at?: string;
  resolved_at?: string;
}

export interface Agent {
  agent_id: string;
  name: string;
  status: 'idle' | 'busy' | 'paused' | 'awaiting_plan_confirmation' | 'awaiting_confirmation' | 'error';
  current_bug_id?: string;
  worktree_path?: string;
  created_at?: string;
  updated_at?: string;
}

export interface AnalysisReport {
  symptom?: { expected?: string; actual?: string; steps?: string[]; acceptance?: string[] };
  open_questions?: string[];
  hypotheses?: { description: string; evidence: string; status: 'confirmed' | 'rejected' }[];
  root_cause?: string;
  trigger_paths?: string[];
}

export interface AgentStatus {
  agent_id: string;
  name: string;
  status: string;
  current_bug?: Bug;
  current_stage?: string;
  progress: number;
  logs: string[];
  worktree_path?: string;
  repair_branch?: string;
  base_commit?: string;
  git_commit_hash?: string;
  modified_files?: string[];
  root_cause?: string;
  confidence?: string;
  analysis?: AnalysisReport;
  repro?: { command?: string; reproduced?: boolean; exit_before?: number; not_reproducible_reason?: string; test_files?: string[] };
  review?: { status?: string; summary?: string; findings?: { location: string; detail: string }[] };
  fix_summary?: string;
  error?: string;
  test_results?: { passed?: boolean; command?: string; exit_code?: number; output?: string };
  confirmation?: { decision_id: string; status: string; user_choice?: { confirmed: boolean; feedback: string; images?: string[]; merge?: { cleanup_pending?: boolean; cleanup_error?: string }; yunxiao_sync?: { status: 'pending' | 'succeeded' | 'failed'; target: string; error?: string } }; answered_at?: string };
}

export interface AgentTranscriptEntry {
  id: number;
  level: 'thinking' | 'assistant' | 'info' | 'warning' | 'error' | string;
  message: string;
  metadata?: { label?: string; duration_ms?: number } | null;
  created_at: string;
}

export interface AgentLiveStream {
  label: string;
  thinking: string;
  content: string;
  content_chars: number;
  elapsed_ms: number;
  thinking_ms: number;
}

export interface LLMProvider {
  id: string;
  name: string;
  type: string;
  base_url: string;
  api_key: string;
  available_models: string[];
  default_model: string;
  enabled: boolean;
}

export interface StageConfig {
  provider: string;
  model: string;
  temperature: number;
  max_tokens: number;
}

export interface LLMConfig {
  providers: LLMProvider[];
  default_provider: string;
  default_model: string;
  use_stage_config: boolean;
  response_style: 'detailed' | 'concise' | 'minimal';
  stage_config?: Record<string, StageConfig>;
}

export interface YunxiaoConfig {
  api_base: string;
  org_id: string;
  project_id: string;
  token_configured: boolean;
}

export interface WebSocketMessage {
  type: string;
  agent_id?: string;
  data?: any;
  log?: string;
  level?: string;
}

export interface TaskSubmitRequest {
  bug_id: string;
  priority: number;
  supplement_revision?: number;
}

export interface BugSupplement {
  notes: string;
  images: string[];
  revision: number;
  image_warning?: string;
  document?: RichInputNode;
}

export interface RichInputNode {
  type: string;
  text?: string;
  attrs?: { image_index?: number; start?: number };
  marks?: { type: string }[];
  content?: RichInputNode[];
}

export interface RichInputValue {
  text: string;
  images: string[];
  document?: RichInputNode;
}

export interface Decision {
  decision_id: string;
  bug_id: string;
  agent_id: string;
  stage: string;
  question: string;
  options: any[];
  user_choice?: any;
  status: 'pending' | 'answered' | 'timeout';
  created_at?: string;
  answered_at?: string;
}
