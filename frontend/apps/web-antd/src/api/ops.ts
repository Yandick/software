import { requestClient } from '#/api/request';

export function askQuestion(question: string) {
  return requestClient.post<any>('/qa/ask', { question });
}

export function suggestQuestions(q = '') {
  return requestClient.get<any[]>('/qa/suggest', { params: { q } });
}

export function listIssues() {
  return requestClient.get<any[]>('/issues');
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

export function listAccounts() {
  return requestClient.get<any[]>('/accounts');
}

export function createAccount(data: Record<string, any>) {
  return requestClient.post<any>('/accounts', data);
}

export function freezeAccount(id: number) {
  return requestClient.post<any>(`/accounts/${id}/freeze`);
}

export function unfreezeAccount(id: number) {
  return requestClient.post<any>(`/accounts/${id}/unfreeze`);
}

export function listKnowledge() {
  return requestClient.get<any[]>('/knowledge');
}

export function createKnowledge(data: Record<string, any>) {
  return requestClient.post<any>('/knowledge', data);
}

export function getStats() {
  return requestClient.get<any>('/audit/stats');
}

export function getAuditLogs() {
  return requestClient.get<any>('/audit/logs');
}
