import { requestClient } from '#/api/request';

export function askQuestion(question: string, enableThinking = false, conversationId?: number | null) {
  return requestClient.post<any>('/qa/ask', {
    conversation_id: conversationId || undefined,
    enable_thinking: enableThinking,
    question,
  });
}

export function suggestQuestions(q = '') {
  return requestClient.get<any[]>('/qa/suggest', { params: { q } });
}

export function listQaConversations(limit = 20) {
  return requestClient.get<any[]>('/qa/conversations', { params: { limit } });
}

export function getQaConversation(id: number) {
  return requestClient.get<any>(`/qa/conversations/${id}`);
}

export function getLlmStatus() {
  return requestClient.get<any>('/llm/status');
}

export function listIssues(status = '', q = '') {
  return requestClient.get<any[]>('/issues', { params: { q, status } });
}

export function createIssue(data: Record<string, any>) {
  return requestClient.post<any>('/issues', data);
}

export function draftIssue(description: string) {
  return requestClient.post<any>('/issues/draft', { description });
}

export function uploadIssueAttachment(file: File) {
  const data = new FormData();
  data.append('file', file);
  return requestClient.post<any>('/issues/attachments', data, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
}

export function acceptIssue(id: number) {
  return requestClient.post<any>(`/issues/${id}/accept`);
}

export function changeIssueStatus(id: number, status: string, note = '') {
  return requestClient.post<any>(`/issues/${id}/status`, { note, status });
}

export function handleIssue(id: number, solution: string) {
  return requestClient.post<any>(`/issues/${id}/handle`, { solution });
}

export function visitIssue(id: number, data: Record<string, any>) {
  return requestClient.post<any>(`/issues/${id}/visit`, data);
}

export function feedbackIssue(id: number, data: Record<string, any>) {
  return requestClient.post<any>(`/issues/${id}/feedback`, data);
}

export function assistIssue(id: number) {
  return requestClient.get<any>(`/issues/${id}/assist`);
}

export function createIssueKnowledgeCandidate(id: number) {
  return requestClient.post<any>(`/issues/${id}/knowledge-candidate`);
}

export function listAccounts(q = '') {
  return requestClient.get<any[]>('/accounts', { params: { q } });
}

export function exportAccounts(q = '') {
  return requestClient.get<any>('/accounts/export', { params: { q } });
}

export function createAccount(data: Record<string, any>) {
  return requestClient.post<any>('/accounts', data);
}

export function updateAccount(id: number, data: Record<string, any>) {
  return requestClient.put<any>(`/accounts/${id}`, data);
}

export function freezeAccount(id: number) {
  return requestClient.post<any>(`/accounts/${id}/freeze`);
}

export function unfreezeAccount(id: number) {
  return requestClient.post<any>(`/accounts/${id}/unfreeze`);
}

export function listAccountApprovals(status = '') {
  return requestClient.get<any[]>('/account-approvals', { params: { status } });
}

export function createAccountApproval(data: Record<string, any>) {
  return requestClient.post<any>('/account-approvals', data);
}

export function decideAccountApproval(id: number, decision: string, reason = '') {
  return requestClient.post<any>(`/account-approvals/${id}/decision`, { decision, reason });
}

export function listKnowledge(params: Record<string, any> = {}) {
  return requestClient.get<any[]>('/knowledge', { params });
}

export function createKnowledge(data: Record<string, any>) {
  return requestClient.post<any>('/knowledge', data);
}

export function updateKnowledge(id: number, data: Record<string, any>) {
  return requestClient.put<any>(`/knowledge/${id}`, data);
}

export function changeKnowledgeStatus(id: number, status: string, reviewNote = '') {
  return requestClient.post<any>(`/knowledge/${id}/status`, { review_note: reviewNote, status });
}

export function getStats() {
  return requestClient.get<any>('/audit/stats');
}

export function getAuditLogs(params: Record<string, any> = {}) {
  return requestClient.get<any>('/audit/logs', { params });
}

export function evaluateRag() {
  return requestClient.get<any>('/rag/evaluate');
}

export function createDemoSession() {
  return requestClient.post<any>('/demo/session');
}

export function getDemoSession(id: string) {
  return requestClient.get<any>(`/demo/session/${id}`);
}

export function runDemoStep(id: string) {
  return requestClient.post<any>(`/demo/session/${id}/step`);
}

export function resetDemoSession(id: string) {
  return requestClient.post<any>(`/demo/session/${id}/reset`);
}
