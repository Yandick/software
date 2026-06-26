import { useAccessStore } from '@vben/stores';

import { requestClient } from '#/api/request';

export type ApiPayload = Record<string, unknown>;

export interface RagScoreDetail {
  embedding?: number;
  final?: number;
  keyword_hits?: number;
  lexical?: number;
  matched_token_count?: number;
  overlap?: number;
  phrase?: number;
  rerank_boost?: number;
  retriever?: string;
  tags?: number;
  title?: number;
}

export interface RagReference {
  chunk_index?: number;
  id: number;
  match_reason?: string;
  matched_terms?: string[];
  retrieval_stage?: string;
  score: number;
  score_detail?: RagScoreDetail;
  snippet?: string;
  source_type?: string;
  tags?: string;
  title: string;
  updated_at?: string;
  version?: number;
}

export interface RagMetadata {
  confidence: number;
  query_terms: string[];
  strategy: string;
}

export interface NextAction {
  enabled: boolean;
  key: string;
  label: string;
}

export interface IssueDraft {
  attachment_url?: string;
  category: string;
  confidence?: number;
  contact_phone?: string;
  description: string;
  extraction_error?: string;
  extraction_source?: string;
  impact_scope?: string;
  llm_status?: string;
  log_excerpt?: string;
  missing_fields: string[];
  priority: string;
  title: string;
}

export interface AgentTraceStep {
  agent: string;
  observation?: ApiPayload;
  phase?: string;
  prompt_loaded?: boolean;
  prompt_path?: string;
  thought?: string;
  tool: string;
}

export interface AgentWorkflowResult {
  agents?: ApiPayload[];
  decision?: ApiPayload;
  evaluator?: ApiPayload;
  intent_route?: IntentRoute;
  knowledge_curator?: ApiPayload;
  llm_reviews?: ApiPayload;
  mode: string;
  risk?: ApiPayload;
  tools_used?: string[];
  trace: AgentTraceStep[];
}

export interface IntentRoute {
  confidence: number;
  evidence?: ApiPayload;
  intent: string;
  intent_label: string;
  kind: string;
  reason?: string;
  risk_level: string;
  should_handoff: boolean;
  should_rag: boolean;
  source?: string;
}

export interface QaAskResponse {
  agent: AgentWorkflowResult;
  answer: string;
  automation_summary: string[];
  clarification_questions: string[];
  confidence: number;
  conversation_id: number;
  employee: ApiPayload;
  handoff_reasons: string[];
  intent: string;
  intent_label: string;
  intent_route?: IntentRoute;
  issue_draft: IssueDraft;
  llm_used: boolean;
  missing_fields: string[];
  model_status: string;
  need_human: boolean;
  next_actions: NextAction[];
  rag: RagMetadata;
  reasoning_available: boolean;
  reasoning_enabled: boolean;
  references: RagReference[];
  risk_level: string;
}

export interface RagSuggestion {
  id: number | string;
  matched_terms?: string[];
  query: string;
  score?: number;
  snippet?: string;
  source_type?: string;
  tags?: string[];
  title: string;
}

export interface QaConversationSummary {
  created_at: string;
  id: number;
  last_message?: string;
  last_message_at?: string;
  message_count?: number;
  status?: string;
  title: string;
  updated_at: string;
  user_id?: number;
  user_name?: string;
}

export interface QaMessage {
  content: string;
  created_at: string;
  id: number;
  metadata: ApiPayload;
  role: 'assistant' | 'system' | 'user';
}

export interface QaConversationDetail {
  conversation: QaConversationSummary;
  messages: QaMessage[];
}

export interface LlmStatus {
  employee_name?: string;
  ready?: boolean;
  status?: string;
  vllm_base_url?: string;
  vllm_model_name?: string;
  [key: string]: unknown;
}

export interface IssueItem {
  attachment_url?: string;
  category: string;
  contact_phone?: string;
  created_at?: string;
  description: string;
  id: number;
  impact_scope?: string;
  log_excerpt?: string;
  priority: string;
  solution?: string;
  status: string;
  title: string;
  updated_at?: string;
  user_feedback?: string;
  user_satisfaction_score?: number;
}

export interface AttachmentUploadResponse {
  filename: string;
  url: string;
}

export interface IssueAssist {
  references?: RagReference[];
  suggested_steps?: string[];
  [key: string]: unknown;
}

export interface AccountItem {
  account_name: string;
  contact_phone?: string;
  department?: string;
  expires_at?: string;
  expiry_status?: string;
  id: number;
  owner_name?: string;
  permission_scope?: string;
  remark?: string;
  risk_level?: string;
  status: string;
  updated_at?: string;
}

export interface AccountApprovalItem {
  action: string;
  account_id: number;
  id: number;
  payload?: ApiPayload;
  reason?: string;
  requested_by?: number;
  status: string;
}

export interface StaffUserItem {
  created_at?: string;
  department?: string;
  id: number;
  real_name: string;
  role: string;
  status: string;
  username: string;
}

export interface CsvExportResponse {
  content: string;
  count?: number;
  filename: string;
}

export interface SensitiveFinding {
  count: number;
  label: string;
  samples?: string[];
  severity: string;
  type?: string;
}

export interface KnowledgeSensitiveCheck {
  blocking: boolean;
  findings: SensitiveFinding[];
  has_sensitive: boolean;
  redacted?: {
    content?: string;
    tags?: string;
    title?: string;
  };
}

export interface KnowledgeDuplicateCandidate {
  approx_similarity?: number;
  containment?: number;
  embedding_similarity?: number;
  id: number;
  relation: string;
  score: number;
  semantic_relation?: string;
  title: string;
}

export interface KnowledgeDuplicateCheck {
  blocking: boolean;
  candidates: KnowledgeDuplicateCandidate[];
  decision: 'exact_duplicate' | 'near_duplicate' | 'unique' | string;
  embedding?: ApiPayload;
  message?: string;
  policy?: ApiPayload;
}

export interface KnowledgeItem {
  content: string;
  created_at?: string;
  duplicate_check?: KnowledgeDuplicateCheck;
  id: number;
  reviewed_at?: string;
  reviewed_by?: number;
  review_note?: string;
  sensitive_check?: KnowledgeSensitiveCheck;
  source_type: string;
  status: string;
  tags: string;
  title: string;
  updated_at?: string;
  version: number;
}

export interface AutonomousIngestResponse {
  action: string;
  duplicate_check?: KnowledgeDuplicateCheck;
  item?: KnowledgeItem;
  novel_units?: string[];
  policy?: ApiPayload;
}

export interface KnowledgeDocumentImportResponse {
  chunk_count: number;
  redacted_count: number;
  skipped_count?: number;
}

export interface AuditStats {
  active_accounts?: number;
  average_rag_confidence?: number;
  closed_issues?: number;
  frozen_accounts?: number;
  handled_issues?: number;
  human_transfer_rate?: number;
  pending_issues?: number;
  pending_knowledge?: number;
  published_knowledge?: number;
  self_solved_rate?: number;
  total_qa?: number;
  [key: string]: unknown;
}

export interface AuditLogsResponse {
  audit: ApiPayload[];
  event_summary: ApiPayload[];
  qa: ApiPayload[];
  target_summary: ApiPayload[];
}

export interface RagEvaluationCase {
  confidence: number;
  expected: string[];
  passed: boolean;
  query: string;
  query_terms: string[];
  references: RagReference[];
}

export interface RagEvaluation {
  cases: RagEvaluationCase[];
  pass_rate: number;
  passed: number;
  strategy: string;
  total: number;
}

export function askQuestion(question: string, enableThinking = false, conversationId?: number | null) {
  return requestClient.post<QaAskResponse>('/qa/ask', {
    conversation_id: conversationId || undefined,
    enable_thinking: enableThinking,
    question,
  });
}

export function suggestQuestions(q = '') {
  return requestClient.get<RagSuggestion[]>('/qa/suggest', { params: { q } });
}

export function listQaConversations(limit = 20) {
  return requestClient.get<QaConversationSummary[]>('/qa/conversations', { params: { limit } });
}

export function getQaConversation(id: number) {
  return requestClient.get<QaConversationDetail>(`/qa/conversations/${id}`);
}

export function deleteQaConversation(id: number) {
  return requestClient.delete<{ deleted: boolean; deleted_at: string; id: number }>(`/qa/conversations/${id}`);
}

export function getLlmStatus() {
  return requestClient.get<LlmStatus>('/llm/status');
}

export function listIssues(status = '', q = '') {
  return requestClient.get<IssueItem[]>('/issues', { params: { q, status } });
}

export function createIssue(data: ApiPayload) {
  return requestClient.post<IssueItem>('/issues', data);
}

export function draftIssue(description: string) {
  return requestClient.post<IssueDraft>('/issues/draft', { description });
}

export function uploadIssueAttachment(file: File) {
  const data = new FormData();
  data.append('file', file);
  return requestClient.post<AttachmentUploadResponse>('/issues/attachments', data, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
}

export function isProtectedIssueAttachment(url = '') {
  return /^(?:https?:\/\/[^/]+)?\/api\/issues\/attachments\/\d+\/download(?:[?#].*)?$/.test(url);
}

function filenameFromDisposition(disposition = '') {
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    return decodeURIComponent(utf8Match[1]);
  }
  const asciiMatch = disposition.match(/filename="?([^";]+)"?/i);
  return asciiMatch?.[1] ? decodeURIComponent(asciiMatch[1]) : '';
}

export async function downloadIssueAttachment(url: string) {
  const accessStore = useAccessStore();
  const response = await fetch(url, {
    headers: accessStore.accessToken
      ? { Authorization: `Bearer ${accessStore.accessToken}` }
      : {},
  });
  if (!response.ok) {
    let detail = '';
    try {
      detail = (await response.json())?.detail || '';
    } catch {
      detail = await response.text();
    }
    throw new Error(detail || `附件下载失败：HTTP ${response.status}`);
  }
  const blob = await response.blob();
  const filename =
    filenameFromDisposition(response.headers.get('content-disposition') || '') ||
    decodeURIComponent(url.split('?')[0]?.split('/').pop() || 'attachment');
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}

export function acceptIssue(id: number) {
  return requestClient.post<IssueItem>(`/issues/${id}/accept`);
}

export function changeIssueStatus(id: number, status: string, note = '') {
  return requestClient.post<IssueItem>(`/issues/${id}/status`, { note, status });
}

export function handleIssue(id: number, solution: string) {
  return requestClient.post<IssueItem>(`/issues/${id}/handle`, { solution });
}

export function feedbackIssue(id: number, data: ApiPayload) {
  return requestClient.post<IssueItem>(`/issues/${id}/feedback`, data);
}

export function assistIssue(id: number) {
  return requestClient.get<IssueAssist>(`/issues/${id}/assist`);
}

export function createIssueKnowledgeCandidate(id: number) {
  return requestClient.post<KnowledgeItem>(`/issues/${id}/knowledge-candidate`);
}

export function listStaffUsers(q = '', role = '', status = '') {
  return requestClient.get<StaffUserItem[]>('/staff-users', { params: { q, role, status } });
}

export function createStaffUser(data: ApiPayload) {
  return requestClient.post<StaffUserItem>('/staff-users', data);
}

export function updateStaffUser(id: number, data: ApiPayload) {
  return requestClient.put<StaffUserItem>(`/staff-users/${id}`, data);
}

export function freezeStaffUser(id: number) {
  return requestClient.post<StaffUserItem>(`/staff-users/${id}/freeze`);
}

export function unfreezeStaffUser(id: number) {
  return requestClient.post<StaffUserItem>(`/staff-users/${id}/unfreeze`);
}

export function resetStaffUserPassword(id: number, password: string) {
  return requestClient.post<{ id: number; password_reset: boolean }>(`/staff-users/${id}/reset-password`, { password });
}

export function listAccounts(q = '') {
  return requestClient.get<AccountItem[]>('/accounts', { params: { q } });
}

export function exportAccounts(q = '') {
  return requestClient.get<CsvExportResponse>('/accounts/export', { params: { q } });
}

export function createAccount(data: ApiPayload) {
  return requestClient.post<AccountItem>('/accounts', data);
}

export function updateAccount(id: number, data: ApiPayload) {
  return requestClient.put<AccountItem>(`/accounts/${id}`, data);
}

export function freezeAccount(id: number) {
  return requestClient.post<AccountItem>(`/accounts/${id}/freeze`);
}

export function unfreezeAccount(id: number) {
  return requestClient.post<AccountItem>(`/accounts/${id}/unfreeze`);
}

export function listAccountApprovals(status = '') {
  return requestClient.get<AccountApprovalItem[]>('/account-approvals', { params: { status } });
}

export function createAccountApproval(data: ApiPayload) {
  return requestClient.post<AccountApprovalItem>('/account-approvals', data);
}

export function decideAccountApproval(id: number, decision: string, reason = '') {
  return requestClient.post<AccountApprovalItem>(`/account-approvals/${id}/decision`, { decision, reason });
}

export function listKnowledge(params: ApiPayload = {}) {
  return requestClient.get<KnowledgeItem[]>('/knowledge', { params });
}

export function createKnowledge(data: ApiPayload) {
  return requestClient.post<KnowledgeItem>('/knowledge', data);
}

export function autonomousIngestKnowledge(data: ApiPayload) {
  return requestClient.post<AutonomousIngestResponse>('/knowledge/autonomous-ingest', data);
}

export function updateKnowledge(id: number, data: ApiPayload) {
  return requestClient.put<KnowledgeItem>(`/knowledge/${id}`, data);
}

export function changeKnowledgeStatus(id: number, status: string, reviewNote = '') {
  return requestClient.post<KnowledgeItem>(`/knowledge/${id}/status`, { review_note: reviewNote, status });
}

export function deleteKnowledge(id: number) {
  return requestClient.delete<{ deleted: boolean; id: number; status: string; title: string }>(`/knowledge/${id}`);
}

export function checkKnowledgeSensitive(data: ApiPayload) {
  return requestClient.post<KnowledgeSensitiveCheck>('/knowledge/sensitive-check', data);
}

export function checkKnowledgeDuplicate(data: ApiPayload) {
  return requestClient.post<KnowledgeDuplicateCheck>('/knowledge/duplicate-check', data);
}

export function uploadKnowledgeDocument(file: File, data: ApiPayload = {}) {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('title', String(data.title ?? ''));
  formData.append('tags', String(data.tags ?? ''));
  return requestClient.post<KnowledgeDocumentImportResponse>('/knowledge/documents/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
}

export function getStats() {
  return requestClient.get<AuditStats>('/audit/stats');
}

export function getAuditLogs(params: ApiPayload = {}) {
  return requestClient.get<AuditLogsResponse>('/audit/logs', { params });
}

export function exportAuditLogs(params: ApiPayload = {}) {
  return requestClient.get<CsvExportResponse>('/audit/export', { params });
}

export function evaluateRag() {
  return requestClient.get<RagEvaluation>('/rag/evaluate');
}
