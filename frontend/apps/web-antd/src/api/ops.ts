import { requestClient } from '#/api/request';

export function askQuestion(question: string) {
  return requestClient.post<any>('/qa/ask', { question });
}

export function suggestQuestions(q = '') {
  return requestClient.get<any[]>('/qa/suggest', { params: { q } });
}

export function getLlmStatus() {
  return requestClient.get<any>('/llm/status');
}

export function listIssues(status = '') {
  return requestClient.get<any[]>('/issues', { params: { status } });
}

export function createIssue(data: Record<string, any>) {
  return requestClient.post<any>('/issues', data);
}

export function handleIssue(id: number, solution: string) {
  return requestClient.post<any>(`/issues/${id}/handle`, { solution });
}

export function visitIssue(id: number, data: Record<string, any>) {
  return requestClient.post<any>(`/issues/${id}/visit`, data);
}

export function listAccounts(q = '') {
  return requestClient.get<any[]>('/accounts', { params: { q } });
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

export function listKnowledge(params: Record<string, any> = {}) {
  return requestClient.get<any[]>('/knowledge', { params });
}

export function createKnowledge(data: Record<string, any>) {
  return requestClient.post<any>('/knowledge', data);
}

export function updateKnowledge(id: number, data: Record<string, any>) {
  return requestClient.put<any>(`/knowledge/${id}`, data);
}

export function changeKnowledgeStatus(id: number, status: string) {
  return requestClient.post<any>(`/knowledge/${id}/status`, { status });
}

export function getStats() {
  return requestClient.get<any>('/audit/stats');
}

export function getAuditLogs() {
  return requestClient.get<any>('/audit/logs');
}
