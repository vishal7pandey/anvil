export type EventType =
  | 'run.status'
  | 'message.delta'
  | 'message'
  | 'plan'
  | 'tool.call'
  | 'tool.result'
  | 'sandbox.exec'
  | 'run.record'
  | 'diff'
  | 'approval.request'
  | 'subagent'
  | 'usage'
  | 'context.compacted'
  | 'artifact';

export interface StreamEvent {
  run_id: string;
  seq: number;
  ts: string;
  type: EventType;
  agent: string | null;
  data: Record<string, unknown>;
}

export interface RunStatusEvent {
  status:
    | 'queued'
    | 'running'
    | 'waiting_approval'
    | 'handoff'
    | 'succeeded'
    | 'failed'
    | 'cancelled';
}

export interface MessageEvent {
  role: 'user' | 'assistant' | 'system';
  text: string;
}

export interface PlanEvent {
  todos: Array<{ content: string; status: 'in_progress' | 'completed' | 'pending' }>;
}

export interface ToolCallEvent {
  call_id: string;
  name: string;
  args: Record<string, unknown>;
}

export interface ToolResultEvent {
  call_id: string;
  ok: boolean;
  output: string;
  duration_ms: number;
}

export interface SandboxExecEvent {
  cmd: string;
  exit_code: number;
  output_tail: string;
}

export interface DiffEvent {
  files: Array<{ path: string; patch: string }>;
}

export interface ApprovalInboxItem extends ApprovalRequest {
  run_id: string;
  session_id: string;
  session_title: string | null;
  repo: string;
  run_status: string;
  requested_at: string;
}

export interface SubagentEvent {
  name: string;
  phase: 'start' | 'end';
  summary: string;
}

export interface UsageEvent {
  model_alias: string;
  input_tokens: number;
  output_tokens: number;
  usd: number;
}

export interface ArtifactEvent {
  kind: 'pr' | 'jira_comment' | 'confluence_page' | 'sbom' | 'report' | 'handoff';
  url?: string | null;
  title?: string;
  note?: Record<string, string>;
  markdown?: string;
}

export interface Session {
  id: string;
  repo: string;
  environment: string;
  mode: string;
  merge_policy: string;
  model_profile: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface Run {
  id: string;
  session_id: string;
  trigger: string;
  repo: string;
  status:
    | 'queued'
    | 'running'
    | 'waiting_approval'
    | 'handoff'
    | 'succeeded'
    | 'failed'
    | 'cancelled';
  mode: string;
  network: string;
  base_ref: string;
  jira_key: string | null;
  base_sha: string | null;
  branch: string | null;
  sandbox_image_digest: string | null;
  model_aliases: Record<string, unknown>;
  skill_versions: Record<string, unknown>;
  prompt_hash: string | null;
  budgets: Record<string, unknown>;
  started_at: string | null;
  ended_at: string | null;
  usd: number;
  cost_usd?: number | null;
  cost_status?: 'pending' | 'available' | 'missing';
  input_tokens: number;
  output_tokens: number;
  human_touches: number;
  wall_time_seconds?: number | null;
  model_calls?: number | null;
  ci_rounds?: number;
  outcome?: string | null;
  artifacts: ArtifactEvent[];
  run_record: Record<string, unknown>;
  cancel_requested_at: string | null;
  error: string | null;
  created_at: string;
}

export interface Metrics {
  from: string | null;
  to: string | null;
  scenario: string | null;
  run_count: number;
  total_wall_time_seconds: number;
  average_wall_time_seconds: number | null;
  model_calls: number | null;
  input_tokens: number;
  output_tokens: number;
  usd: number | null;
  cost_usd: number | null;
  cost_status: 'available' | 'partial' | 'missing';
  runs_missing_cost: number;
  human_touches: number;
  ci_rounds: number;
  outcomes: Record<string, number>;
}

export interface NewSessionForm {
  repo: string;
  environment: string;
  mode: string;
  model_profile: string;
  merge_policy?: 'human' | 'agent-after-approval' | 'agent';
  title?: string;
}

export interface ApprovalDecision {
  decision: 'approve' | 'reject';
  comment?: string;
}

/** An `approval.request` event as the UI shows it: the event payload plus the emitting agent. */
export interface ApprovalRequest {
  approval_id: string;
  action: string;
  args: Record<string, unknown>;
  reason: string | null;
  agent: string;
}
