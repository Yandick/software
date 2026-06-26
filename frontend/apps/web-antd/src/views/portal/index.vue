<script lang="ts" setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { IconifyIcon } from '@vben/icons';
import { useAccessStore, useUserStore } from '@vben/stores';

import { message, Modal } from 'ant-design-vue';

import {
  askQuestion,
  createIssue,
  deleteQaConversation,
  draftIssue,
  downloadIssueAttachment,
  feedbackIssue,
  getLlmStatus,
  getQaConversation,
  getStats,
  isProtectedIssueAttachment,
  listIssues,
  listQaConversations,
  suggestQuestions,
  uploadIssueAttachment,
} from '#/api/ops';
import { useAutoRefresh } from '#/composables/use-auto-refresh';
import { useAuthStore } from '#/store';

type ChatRole = 'assistant' | 'system' | 'user';
type PortalView = 'chat' | 'issues';

interface ChatMessage {
  agentMode?: string;
  automationSummary?: string[];
  clarificationQuestions?: string[];
  confidence?: number;
  createdAt: string;
  handoffReasons?: string[];
  id: string;
  intentLabel?: string;
  issueDraft?: Record<string, any>;
  llmUsed?: boolean;
  loading?: boolean;
  missingFields?: string[];
  needHuman?: boolean;
  nextActions?: any[];
  question?: string;
  rag?: any;
  references?: any[];
  riskLevel?: string;
  role: ChatRole;
  status?: string;
  streaming?: boolean;
  text: string;
}

interface QaConversation {
  created_at: string;
  id: number;
  last_message?: string;
  message_count?: number;
  title: string;
  updated_at: string;
  user_name?: string;
}

const router = useRouter();
const route = useRoute();
const authStore = useAuthStore();
const accessStore = useAccessStore();
const userStore = useUserStore();

const activeView = ref<PortalView>('chat');
const loginPanelRef = ref<HTMLElement | null>(null);
const portalLoginMode = ref<'staff' | 'user'>(route.query.identity === 'staff' ? 'staff' : 'user');
const portalLoginForm = ref({ password: '', username: '' });
const question = ref('');
const currentQuestion = ref('');
const conversationId = ref<number | null>(null);
const conversationTitle = ref('新会话');
const loading = ref(false);
const creatingIssue = ref(false);
const conversationLoading = ref(false);
const deletingConversationId = ref<number | null>(null);
const restoringConversation = ref(false);
const currentIssueDraft = ref<Record<string, any> | null>(null);
const needHuman = ref(false);
const stats = ref<any>({});
const llmStatus = ref<any>({});
const suggestions = ref<any[]>([]);
const conversations = ref<QaConversation[]>([]);
const issues = ref<any[]>([]);
const issuesLoading = ref(false);
const issueStatus = ref('');
const feedbackOpen = ref(false);
const feedbackSubmitting = ref(false);
const feedbackForm = ref({ feedback: '', id: 0, satisfaction_score: 5 });
const handoffOpen = ref(false);
const handoffPreparing = ref(false);
const handoffUploading = ref(false);
const handoffForm = ref({
  attachment_url: '',
  category: 'general',
  contact_phone: '',
  description: '',
  impact_scope: '',
  log_excerpt: '',
  priority: 'medium',
  title: '',
});
const chatBodyRef = ref<HTMLElement | null>(null);
const inputRef = ref<HTMLTextAreaElement | null>(null);
const autoScrollToBottom = ref(true);
let suggestTimer: ReturnType<typeof setTimeout> | undefined;
let answerTimerToken = 0;

const quickActions = [
  {
    desc: '验证码、认证器、换手机和验证失败',
    icon: 'lucide:shield-check',
    query: 'MFA 验证码收不到，刚换过手机，应该怎么处理？',
    title: 'MFA 验证',
  },
  {
    desc: 'VPN、证书、远程办公和内网访问',
    icon: 'lucide:wifi',
    query: 'VPN 连不上或提示证书过期，我该先检查什么？',
    title: 'VPN 远程办公',
  },
  {
    desc: 'Outlook 离线、收不到邮件和退信',
    icon: 'lucide:mail-warning',
    query: 'Outlook 一直离线，收不到邮件怎么排查？',
    title: '邮箱收发',
  },
  {
    desc: '队列卡住、离线、驱动和区域打印',
    icon: 'lucide:printer',
    query: '打印机任务卡在队列里，无法打印怎么办？',
    title: '打印机',
  },
  {
    desc: '白屏、403、500、502、504 和超时',
    icon: 'lucide:monitor-alert',
    query: '业务系统白屏或提示 500 超时，我该怎么处理？',
    title: '业务系统',
  },
  {
    desc: '共享盘、网盘、路径和访问被拒绝',
    icon: 'lucide:folder-lock',
    query: '共享盘提示访问被拒绝，应该怎么排查？',
    title: '共享盘',
  },
];

const sourceTypeMeta: Record<string, string> = {
  case: '案例',
  document: '文档',
  faq: 'FAQ',
  local: '本地推荐',
  manual: '手册',
  policy: '流程',
  runbook: 'Runbook',
};

const statusOptions = [
  { label: '全部', value: '' },
  { label: '已提交', value: 'submitted' },
  { label: '已受理', value: 'accepted' },
  { label: '处理中', value: 'processing' },
  { label: '待补充', value: 'need_user_info' },
  { label: '待回访', value: 'pending_visit' },
  { label: '已关闭', value: 'closed' },
];

const statusMeta: Record<string, { color: string; label: string; tone: string }> = {
  accepted: { color: 'cyan', label: '已受理', tone: 'active' },
  closed: { color: 'green', label: '已关闭', tone: 'done' },
  handled: { color: 'blue', label: '待回访', tone: 'review' },
  need_user_info: { color: 'purple', label: '待补充', tone: 'blocked' },
  pending: { color: 'orange', label: '待处理', tone: 'pending' },
  pending_visit: { color: 'blue', label: '待回访', tone: 'review' },
  processing: { color: 'geekblue', label: '处理中', tone: 'active' },
  submitted: { color: 'orange', label: '已提交', tone: 'pending' },
};

const priorityMeta: Record<string, { color: string; label: string }> = {
  high: { color: 'red', label: '高' },
  low: { color: 'green', label: '低' },
  medium: { color: 'orange', label: '中' },
};

function createWelcomeMessage(): ChatMessage {
  return {
    createdAt: nowText(),
    id: 'welcome',
    role: 'assistant',
    text: '您好，我是云维。请直接描述你遇到的系统、账号、错误提示和影响范围；我会先给出自助处置建议，必要时整理在线记录并转交运维处理。',
  };
}

const chatMessages = ref<ChatMessage[]>([createWelcomeMessage()]);

const displayName = computed(() => userStore.userInfo?.realName || userStore.userInfo?.username || '当前用户');
const userRole = computed(() => userStore.userInfo?.roles?.[0] || 'user');
const canOpenOps = computed(() => ['admin', 'auditor', 'ops'].includes(userRole.value));
const roleText = computed(() => {
  const labels: Record<string, string> = {
    admin: '管理员',
    auditor: '审计员',
    ops: '运维人员',
    user: '普通用户',
  };
  return labels[userRole.value] || userRole.value;
});

const modelText = computed(() => {
  if (!llmStatus.value.employee_name) return '检测中';
  if (llmStatus.value.ready) return `${llmStatus.value.vllm_model_name || '数字员工'} 在线`;
  return '暂不可用';
});

const statItems = computed(() => [
  { icon: 'lucide:messages-square', label: '我的咨询', value: stats.value.total_qa || 0 },
  { icon: 'lucide:clipboard-list', label: '在线记录', value: stats.value.issues || 0 },
  { icon: 'lucide:loader-circle', label: '处理中', value: stats.value.pending_issues || 0 },
  { icon: 'lucide:check-circle-2', label: '已关闭', value: stats.value.closed_issues || 0 },
]);

const visibleSuggestions = computed(() => suggestions.value.slice(0, 5));
const visibleConversations = computed(() => conversations.value.slice(0, 6));
const activeIssues = computed(() =>
  issues.value.filter((item) => !['closed', 'handled', 'pending_visit'].includes(item.status)).slice(0, 4),
);
const latestIssues = computed(() => issues.value.slice(0, 8));
const isAuthenticated = computed(() => !!accessStore.accessToken && !!userStore.userInfo);
const latestAssistant = computed(() => {
  return [...chatMessages.value].reverse().find((item) => item.role === 'assistant' && !item.loading);
});
const loginModeMeta = computed(() => {
  if (portalLoginMode.value === 'staff') {
    return {
      accountPlaceholder: 'admin / ops / auditor',
      demo: [
        '管理员：admin / admin123',
        '运维人员：ops / ops123',
        '审计员：auditor / audit123',
      ],
      description: '用于进入运维处理台、账号管理、知识维护和统计审计。',
      icon: 'lucide:briefcase-business',
      passwordPlaceholder: '请输入工作人员密码',
      title: '工作人员登录',
      username: 'admin',
    };
  }
  return {
    accountPlaceholder: '例如 user',
    demo: ['普通用户：user / user123'],
    description: '用于咨询数字员工、提交在线记录和查询本人处理进度。',
    icon: 'lucide:circle-user-round',
    passwordPlaceholder: '请输入用户密码',
    title: '用户登录',
    username: 'user',
  };
});

function getChatScrollMetrics() {
  const el = chatBodyRef.value;
  if (!el) {
    return null;
  }
  return {
    clientHeight: el.clientHeight,
    scrollHeight: el.scrollHeight,
    scrollTop: el.scrollTop,
  };
}

function isNearBottom(threshold = 96) {
  const metrics = getChatScrollMetrics();
  if (!metrics) {
    return true;
  }
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= threshold;
}

function handleChatScroll() {
  autoScrollToBottom.value = isNearBottom();
}

function handleChatUserIntent() {
  if (!isNearBottom(24)) {
    autoScrollToBottom.value = false;
  }
}

async function scrollToBottom(force = false) {
  if (!force && !autoScrollToBottom.value) {
    return;
  }
  await nextTick();
  if (chatBodyRef.value) {
    chatBodyRef.value.scrollTo({
      behavior: force ? 'smooth' : 'auto',
      top: chatBodyRef.value.scrollHeight,
    });
  }
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function revealAssistantAnswer(message: ChatMessage, answer: string) {
  const token = ++answerTimerToken;
  const chars = Array.from(answer || '');
  message.loading = false;
  message.streaming = true;
  message.text = '';
  await scrollToBottom(true);
  await sleep(240);

  for (let index = 0; index < chars.length; index += 1) {
    if (token !== answerTimerToken) {
      return;
    }
    const current = chars[index] || '';
    message.text += current;
    const delay = /[。！？!?；;\n]/.test(current) ? 120 : /[，,、]/.test(current) ? 45 : 24;
    if (autoScrollToBottom.value && (index % 3 === 0 || /[。！？!?；;\n，,、]/.test(current))) {
      await scrollToBottom();
    }
    await sleep(delay);
  }

  if (token !== answerTimerToken) {
    return;
  }
  message.streaming = false;
  await scrollToBottom();
}

function nowText() {
  return new Date().toLocaleTimeString();
}

function formatTime(value = '') {
  if (!value) return nowText();
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function statusOf(value = '') {
  return statusMeta[value] || { color: 'default', label: value || '未设置', tone: 'default' };
}

function priorityOf(value = '') {
  return priorityMeta[value] || { color: 'default', label: value || '未设置' };
}

function riskColor(level = '') {
  const colors: Record<string, string> = { high: 'red', low: 'green', medium: 'orange' };
  return colors[level] || 'default';
}

function sourceTypeText(value = '') {
  return sourceTypeMeta[value] || value || '知识';
}

function isLightweightAnswer(item: ChatMessage) {
  return item.status === 'lightweight-intent' || item.rag?.strategy === 'lightweight_intent_no_rag';
}

function showAnswerTags(item: ChatMessage) {
  return item.role === 'assistant' && !item.loading && !item.streaming && !isLightweightAnswer(item);
}

function showDecisionBox(item: ChatMessage) {
  return (
    item.role === 'assistant'
    && !item.loading
    && !item.streaming
    && !isLightweightAnswer(item)
    && Boolean(item.missingFields?.length || item.handoffReasons?.length || item.clarificationQuestions?.length)
  );
}

function nextStepTitle(item?: ChatMessage) {
  if (!item) return '可以先选择一个常用服务，也可以直接描述问题。';
  if (isLightweightAnswer(item)) return '直接告诉我你遇到的系统、账号或报错现象。';
  if (item.needHuman) return '这个问题建议提交在线记录，运维人员会接手处理。';
  if (item.clarificationQuestions?.length) return '补充这些信息后，我能更准确地判断下一步。';
  return '可以按建议先自助处理；如果仍未解决，再提交在线记录。';
}

function conversationPreview(item: QaConversation) {
  return item.last_message || item.title || `会话 #${item.id}`;
}

function conversationLabel(item: QaConversation) {
  return item.title || `会话 #${item.id}`;
}

function escapeHtml(value = '') {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function renderInlineMarkdown(value = '') {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
}

function renderMessageMarkdown(value = '') {
  const lines = String(value || '').replace(/\r\n/g, '\n').split('\n');
  const html: string[] = [];
  let paragraph: string[] = [];
  let listType: 'ol' | 'ul' | '' = '';
  let listItems: string[] = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${paragraph.join('<br>')}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!listType || !listItems.length) return;
    html.push(`<${listType}>${listItems.map((item) => `<li>${item}</li>`).join('')}</${listType}>`);
    listType = '';
    listItems = [];
  };
  const pushListItem = (type: 'ol' | 'ul', content: string) => {
    flushParagraph();
    if (listType && listType !== type) flushList();
    listType = type;
    listItems.push(renderInlineMarkdown(content));
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = trimmed.match(/^#{1,3}\s+(.+)$/) || trimmed.match(/^\*\*([^*]+)\*\*\s*$/);
    if (heading) {
      flushParagraph();
      flushList();
      html.push(`<h3>${renderInlineMarkdown(heading[1] || '')}</h3>`);
      continue;
    }
    const ordered = trimmed.match(/^\d+[.)]\s+(.+)$/);
    if (ordered) {
      pushListItem('ol', ordered[1] || '');
      continue;
    }
    const unordered = trimmed.match(/^[-*]\s+(.+)$/);
    if (unordered) {
      pushListItem('ul', unordered[1] || '');
      continue;
    }
    flushList();
    paragraph.push(renderInlineMarkdown(trimmed));
  }
  flushParagraph();
  flushList();
  return html.join('');
}

function attachmentName(url = '') {
  if (!url) return '';
  const clean = url.split('?')[0] || '';
  return clean.split('/').filter(Boolean).pop() || url;
}

async function openAttachment(url = '') {
  if (!url) return;
  try {
    if (isProtectedIssueAttachment(url)) {
      await downloadIssueAttachment(url);
      return;
    }
    window.open(url, '_blank', 'noopener,noreferrer');
  } catch (error: any) {
    message.error(error?.message || '附件下载失败');
  }
}

function fillQuestion(text: string) {
  question.value = text;
  activeView.value = 'chat';
  void nextTick(() => inputRef.value?.focus());
}

function fillClarification(baseQuestion = '', clarification = '') {
  question.value = `${baseQuestion}\n补充信息：${clarification}`.trim();
  activeView.value = 'chat';
  void nextTick(() => inputRef.value?.focus());
}

function startNewConversation() {
  answerTimerToken += 1;
  autoScrollToBottom.value = true;
  conversationId.value = null;
  conversationTitle.value = '新会话';
  currentQuestion.value = '';
  currentIssueDraft.value = null;
  needHuman.value = false;
  chatMessages.value = [createWelcomeMessage()];
  question.value = '';
  void nextTick(() => inputRef.value?.focus());
}

async function loadLlmStatus() {
  try {
    llmStatus.value = await getLlmStatus();
  } catch {
    llmStatus.value = {};
  }
}

async function loadStats() {
  try {
    stats.value = await getStats();
  } catch {
    stats.value = {};
  }
}

async function loadConversations() {
  conversationLoading.value = true;
  try {
    conversations.value = await listQaConversations(20);
  } catch {
    conversations.value = [];
  } finally {
    conversationLoading.value = false;
  }
}

async function loadIssues() {
  issuesLoading.value = true;
  try {
    issues.value = await listIssues(issueStatus.value);
  } catch {
    issues.value = [];
  } finally {
    issuesLoading.value = false;
  }
}

async function loadPortalData() {
  await Promise.all([loadStats(), loadLlmStatus(), loadSuggestions(''), loadConversations(), loadIssues()]);
  await nextTick();
  inputRef.value?.focus();
}

async function refreshPortalData() {
  if (!isAuthenticated.value) {
    return;
  }
  await Promise.all([loadStats(), loadLlmStatus(), loadConversations(), loadIssues()]);
}

async function loadSuggestions(keyword = question.value) {
  try {
    suggestions.value = await suggestQuestions(keyword.trim());
  } catch {
    suggestions.value = quickActions.map((item, index) => ({
      id: `local-${index}`,
      query: item.query,
      source_type: 'local',
      title: item.title,
    }));
  }
}

async function restoreConversation(id: number) {
  answerTimerToken += 1;
  autoScrollToBottom.value = true;
  restoringConversation.value = true;
  activeView.value = 'chat';
  try {
    const result = await getQaConversation(id);
    const conversation = result.conversation || {};
    let lastUserQuestion = '';
    const restored = (result.messages || []).map((item: any) => {
      const metadata = item.metadata || {};
      if (item.role === 'user') {
        lastUserQuestion = item.content || '';
      }
      return {
        agentMode: metadata.agent?.mode,
        automationSummary: metadata.automation_summary || [],
        clarificationQuestions: metadata.clarification_questions || [],
        confidence: metadata.confidence,
        createdAt: formatTime(item.created_at),
        handoffReasons: metadata.handoff_reasons || [],
        id: `history-${item.id}`,
        intentLabel: metadata.intent_label,
        issueDraft: metadata.issue_draft,
        llmUsed: metadata.llm_used,
        missingFields: metadata.missing_fields || [],
        needHuman: metadata.need_human,
        nextActions: metadata.next_actions || [],
        question: item.role === 'assistant' ? lastUserQuestion : item.content,
        rag: metadata.rag,
        references: metadata.references || [],
        riskLevel: metadata.risk_level,
        role: item.role as ChatRole,
        status: metadata.model_status,
        text: item.content,
      } as ChatMessage;
    });
    conversationId.value = id;
    conversationTitle.value = conversation.title || `会话 #${id}`;
    currentQuestion.value = lastUserQuestion;
    const lastAssistant = [...restored].reverse().find((item: ChatMessage) => item.role === 'assistant');
    currentIssueDraft.value = lastAssistant?.issueDraft || null;
    needHuman.value = !!lastAssistant?.needHuman;
    chatMessages.value = restored.length ? restored : [createWelcomeMessage()];
    await scrollToBottom(true);
  } catch (error: any) {
    message.error(error?.message || '会话恢复失败');
  } finally {
    restoringConversation.value = false;
  }
}

function confirmDeleteConversation(item: QaConversation) {
  const title = conversationLabel(item);
  Modal.confirm({
    cancelText: '取消',
    content: '删除后该咨询会从最近咨询中移除，已产生的问答审计记录仍会保留。',
    okText: '删除',
    okType: 'danger',
    async onOk() {
      deletingConversationId.value = item.id;
      try {
        await deleteQaConversation(item.id);
        conversations.value = conversations.value.filter((conversation) => conversation.id !== item.id);
        if (conversationId.value === item.id) {
          startNewConversation();
        }
        message.success('咨询记录已删除');
        await loadConversations();
      } finally {
        deletingConversationId.value = null;
      }
    },
    title: `删除「${title}」？`,
  });
}

async function ask(text = question.value) {
  const rawQuestion = text.trim();
  if (!rawQuestion) {
    await loadSuggestions('');
    message.info('请先输入问题描述');
    return;
  }

  currentQuestion.value = rawQuestion;
  question.value = '';
  loading.value = true;
  activeView.value = 'chat';
  autoScrollToBottom.value = true;
  answerTimerToken += 1;
  const userMessageId = `u-${Date.now()}`;
  const assistantMessageId = `a-${Date.now()}`;

  chatMessages.value.push({
    createdAt: nowText(),
    id: userMessageId,
    role: 'user',
    text: rawQuestion,
  });
  chatMessages.value.push({
    createdAt: nowText(),
    id: assistantMessageId,
    loading: true,
    role: 'assistant',
    text: '正在理解你的问题...',
  });
  await scrollToBottom(true);

  try {
    const result = await askQuestion(rawQuestion, false, conversationId.value);
    conversationId.value = result.conversation_id || conversationId.value;
    conversationTitle.value = conversationTitle.value === '新会话' ? rawQuestion.slice(0, 40) : conversationTitle.value;
    const assistantMessage = chatMessages.value.find((item) => item.id === assistantMessageId);
    if (assistantMessage) {
      assistantMessage.automationSummary = result.automation_summary || [];
      assistantMessage.clarificationQuestions = result.clarification_questions || [];
      assistantMessage.confidence = result.confidence;
      assistantMessage.handoffReasons = result.handoff_reasons || [];
      assistantMessage.intentLabel = result.intent_label;
      assistantMessage.issueDraft = result.issue_draft;
      assistantMessage.llmUsed = result.llm_used;
      assistantMessage.missingFields = result.missing_fields || [];
      assistantMessage.needHuman = result.need_human;
      assistantMessage.nextActions = result.next_actions || [];
      assistantMessage.question = rawQuestion;
      assistantMessage.rag = result.rag;
      assistantMessage.references = result.references || [];
      assistantMessage.riskLevel = result.risk_level;
      assistantMessage.status = result.model_status;
      await revealAssistantAnswer(assistantMessage, result.answer);
    }
    needHuman.value = result.need_human;
    currentIssueDraft.value = result.issue_draft || null;
    if (result.employee) {
      llmStatus.value = {
        ...llmStatus.value,
        employee_name: result.employee.name,
        employee_role: result.employee.role,
        ready: true,
      };
    }
    await Promise.all([loadStats(), loadConversations(), loadIssues()]);
  } catch (error: any) {
    const assistantMessage = chatMessages.value.find((item) => item.id === assistantMessageId);
    if (assistantMessage) {
      assistantMessage.loading = false;
      assistantMessage.needHuman = true;
      assistantMessage.question = rawQuestion;
      assistantMessage.status = 'unavailable';
      assistantMessage.text = '智能服务暂时不可用。你可以先提交在线记录，运维人员会继续处理。';
    }
    needHuman.value = true;
    message.error(error?.message || '智能服务请求失败');
  } finally {
    loading.value = false;
    await scrollToBottom();
  }
}

async function transferToHuman(text = currentQuestion.value || question.value, preparedDraft?: Record<string, any>) {
  const issueText = text.trim();
  const reusableDraft = preparedDraft || currentIssueDraft.value;
  if (!issueText && !reusableDraft?.description) {
    message.warning('请先描述问题，再提交在线记录');
    return;
  }
  handoffPreparing.value = true;
  try {
    const draft = reusableDraft || (await draftIssue(issueText));
    handoffForm.value = {
      attachment_url: draft.attachment_url || '',
      category: draft.category || 'general',
      contact_phone: draft.contact_phone || '',
      description: draft.description || issueText,
      impact_scope: draft.impact_scope || '',
      log_excerpt: draft.log_excerpt || '',
      priority: needHuman.value ? 'high' : draft.priority || 'medium',
      title: draft.title || issueText.slice(0, 40) || '在线记录',
    };
    handoffOpen.value = true;
  } finally {
    handoffPreparing.value = false;
  }
}

async function beforeHandoffUpload(file: File) {
  handoffUploading.value = true;
  try {
    const result = await uploadIssueAttachment(file);
    handoffForm.value.attachment_url = result.url;
    message.success(`附件已上传：${result.filename}`);
  } finally {
    handoffUploading.value = false;
  }
  return false;
}

async function submitHandoffIssue() {
  if (!handoffForm.value.title.trim() || !handoffForm.value.description.trim()) {
    message.warning('请确认标题和问题描述');
    return;
  }
  creatingIssue.value = true;
  try {
    const result = await createIssue({
      ...handoffForm.value,
      title: handoffForm.value.title.trim(),
      description: handoffForm.value.description.trim(),
    });
    chatMessages.value.push({
      createdAt: nowText(),
      id: `s-${Date.now()}`,
      role: 'system',
      text: `在线记录 #${result.id} 已提交，运维人员会按优先级处理。`,
    });
    handoffOpen.value = false;
    activeView.value = 'issues';
    message.success('在线记录已提交');
    await Promise.all([loadStats(), loadIssues(), scrollToBottom()]);
  } finally {
    creatingIssue.value = false;
  }
}

function openFeedback(item: any) {
  feedbackForm.value = {
    feedback: item.user_feedback || '',
    id: item.id,
    satisfaction_score: item.user_satisfaction_score || item.satisfaction_score || 5,
  };
  feedbackOpen.value = true;
}

async function submitFeedback() {
  feedbackSubmitting.value = true;
  try {
    await feedbackIssue(feedbackForm.value.id, {
      feedback: feedbackForm.value.feedback,
      satisfaction_score: feedbackForm.value.satisfaction_score,
    });
    feedbackOpen.value = false;
    message.success('评价已提交');
    await loadIssues();
  } finally {
    feedbackSubmitting.value = false;
  }
}

async function goOps() {
  await router.push('/ops/dashboard');
}

async function focusLogin(mode: 'staff' | 'user' = portalLoginMode.value) {
  portalLoginMode.value = mode;
  await nextTick();
  loginPanelRef.value?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function fillDemoAndLogin() {
  portalLoginForm.value.username = loginModeMeta.value.username;
  portalLoginForm.value.password =
    portalLoginMode.value === 'staff' ? 'admin123' : 'user123';
  await handlePortalLogin();
}

async function returnPortalHome() {
  await authStore.logout(false, '/portal');
}

async function switchIdentity() {
  const target = canOpenOps.value ? 'user' : 'staff';
  await authStore.logout(false, `/portal?identity=${target}`);
}

async function handlePortalLogin() {
  const username = portalLoginForm.value.username.trim();
  if (!username || !portalLoginForm.value.password) {
    message.warning('请输入账号和密码');
    return;
  }
  let result: Awaited<ReturnType<typeof authStore.authLogin>>;
  try {
    result = await authStore.authLogin(
      {
        password: portalLoginForm.value.password,
        username,
      },
      async () => {},
    );
  } catch {
    portalLoginForm.value.password = '';
    return;
  }
  const role = result.userInfo?.roles?.[0];
  if (['admin', 'auditor', 'ops'].includes(role || '')) {
    message.info('工作人员账号已进入管理台');
    const redirectQuery = Array.isArray(route.query.redirect)
      ? route.query.redirect[0]
      : route.query.redirect;
    let redirectPath = '';
    try {
      redirectPath = redirectQuery ? decodeURIComponent(redirectQuery) : '';
    } catch {
      redirectPath = '';
    }
    await router.replace(redirectPath.startsWith('/ops') ? redirectPath : '/ops/dashboard');
    return;
  }
  portalLoginForm.value.password = '';
  await router.replace('/portal');
  await loadPortalData();
}

async function logout() {
  await authStore.logout(false, '/portal');
}

watch(
  question,
  (value) => {
    if (suggestTimer) clearTimeout(suggestTimer);
    suggestTimer = setTimeout(() => loadSuggestions(value), 180);
  },
);

useAutoRefresh(refreshPortalData, 15000);

onMounted(async () => {
  if (isAuthenticated.value) {
    await loadPortalData();
  }
});

watch(
  isAuthenticated,
  async (value) => {
    if (value) {
      await loadPortalData();
    }
  },
);

watch(
  () => route.query.identity,
  (value) => {
    portalLoginMode.value = value === 'staff' ? 'staff' : 'user';
  },
);

onUnmounted(() => {
  if (suggestTimer) {
    clearTimeout(suggestTimer);
  }
  answerTimerToken += 1;
});
</script>

<template>
  <div class="portal-page">
    <section v-if="!isAuthenticated" class="portal-public">
      <header class="public-topbar">
        <div class="brand-block">
          <span class="brand-mark">云</span>
          <div>
            <strong>云维服务门户</strong>
            <small>企业运维数字员工</small>
          </div>
        </div>
      </header>

      <main class="public-shell">
        <section class="public-intro">
          <div class="public-kicker">
            <IconifyIcon icon="lucide:sparkles" />
            <span>面向业务用户的 IT 运维服务入口</span>
          </div>
          <h1>
            <span>咨询问题</span>
            <span>提交在线记录</span>
            <span>查看处理进度</span>
          </h1>
          <p>
            普通用户从这里进入服务门户，描述账号、VPN、业务系统或数据库中间件问题。
            云维会先给出自助建议，无法解决时整理信息并转交运维人员。
          </p>
          <div class="public-actions">
            <button class="primary-button large" type="button" @click="focusLogin('user')">
              <IconifyIcon icon="lucide:log-in" />
              <span>用户登录</span>
            </button>
            <button class="ghost-button large" type="button" @click="focusLogin('staff')">
              <IconifyIcon icon="lucide:settings" />
              <span>工作人员登录</span>
            </button>
          </div>
          <div class="public-service-grid">
            <article v-for="item in quickActions" :key="item.title" class="public-service-card">
              <IconifyIcon :icon="item.icon" />
              <strong>{{ item.title }}</strong>
              <span>{{ item.desc }}</span>
            </article>
          </div>
        </section>

        <aside ref="loginPanelRef" class="public-login">
          <div class="login-panel">
            <div class="identity-segment" aria-label="选择登录身份">
              <button
                :class="{ active: portalLoginMode === 'user' }"
                type="button"
                @click="portalLoginMode = 'user'"
              >
                <IconifyIcon icon="lucide:user-round" />
                <span>用户</span>
              </button>
              <button
                :class="{ active: portalLoginMode === 'staff' }"
                type="button"
                @click="portalLoginMode = 'staff'"
              >
                <IconifyIcon icon="lucide:briefcase-business" />
                <span>工作人员</span>
              </button>
            </div>
            <div class="panel-heading vertical">
              <h2>{{ loginModeMeta.title }}</h2>
              <p>{{ loginModeMeta.description }}</p>
            </div>
            <a-form layout="vertical" @submit.prevent="handlePortalLogin">
              <a-form-item :label="portalLoginMode === 'staff' ? '工作人员账号' : '用户账号'">
                <a-input
                  v-model:value="portalLoginForm.username"
                  autocomplete="username"
                  data-testid="portal-login-username"
                  :placeholder="loginModeMeta.accountPlaceholder"
                  @press-enter="handlePortalLogin"
                />
              </a-form-item>
              <a-form-item label="密码">
                <a-input-password
                  v-model:value="portalLoginForm.password"
                  autocomplete="current-password"
                  data-testid="portal-login-password"
                  :placeholder="loginModeMeta.passwordPlaceholder"
                  @press-enter="handlePortalLogin"
                />
              </a-form-item>
              <button class="primary-button full large" data-testid="portal-login-submit" :disabled="authStore.loginLoading" type="submit">
                <IconifyIcon :icon="loginModeMeta.icon" />
                <span>{{ authStore.loginLoading ? '登录中' : portalLoginMode === 'staff' ? '进入管理台' : '进入服务门户' }}</span>
              </button>
            </a-form>
            <div class="demo-account">
              <strong>本地演示用户</strong>
              <span v-for="item in loginModeMeta.demo" :key="item">{{ item }}</span>
              <button class="demo-login-button" type="button" @click="fillDemoAndLogin">
                使用当前身份体验
              </button>
              <small>两种身份共用同一后端认证、权限和审计；入口按使用场景分离。</small>
            </div>
          </div>
        </aside>
      </main>
    </section>

    <template v-else>
    <header class="portal-topbar">
      <div class="brand-block">
        <span class="brand-mark">云</span>
        <div>
          <strong>云维服务门户</strong>
          <small>企业运维数字员工</small>
        </div>
      </div>

      <nav class="portal-nav" aria-label="服务视图">
        <button :class="['nav-button', { active: activeView === 'chat' }]" data-testid="portal-nav-chat" type="button" @click="activeView = 'chat'">
          <IconifyIcon icon="lucide:message-circle" />
          <span>咨询</span>
        </button>
        <button :class="['nav-button', { active: activeView === 'issues' }]" data-testid="portal-nav-issues" type="button" @click="activeView = 'issues'">
          <IconifyIcon icon="lucide:clipboard-list" />
          <span>记录</span>
        </button>
      </nav>

      <div class="user-block">
        <div class="identity-actions">
          <button type="button" @click="returnPortalHome">
            <IconifyIcon icon="lucide:home" />
            <span>门户首页</span>
          </button>
          <button class="primary" type="button" @click="switchIdentity">
            <IconifyIcon icon="lucide:shuffle" />
            <span>切换身份</span>
          </button>
        </div>
        <span class="user-chip">
          <strong>{{ displayName }}</strong>
          <small>{{ roleText }}</small>
        </span>
        <a-tooltip v-if="canOpenOps" title="管理台">
          <button class="icon-button" type="button" @click="goOps">
            <IconifyIcon icon="lucide:settings" />
          </button>
        </a-tooltip>
        <a-tooltip title="退出登录">
          <button class="icon-button" type="button" @click="logout">
            <IconifyIcon icon="lucide:log-out" />
          </button>
        </a-tooltip>
      </div>
    </header>

    <main class="portal-content">
      <section class="status-band" aria-label="服务概览">
        <article v-for="item in statItems" :key="item.label" class="stat-tile">
          <IconifyIcon :icon="item.icon" />
          <div>
            <strong>{{ item.value }}</strong>
            <span>{{ item.label }}</span>
          </div>
        </article>
        <article class="model-tile">
          <IconifyIcon icon="lucide:activity" />
          <div>
            <strong>{{ modelText }}</strong>
            <span>{{ conversationId ? `会话 #${conversationId}` : '新会话' }}</span>
          </div>
        </article>
      </section>

      <section v-show="activeView === 'chat'" class="portal-grid">
        <aside class="left-rail">
          <section class="tool-panel service-panel">
            <div class="panel-heading">
              <h2>常用服务</h2>
              <button class="text-button" type="button" @click="startNewConversation">
                <IconifyIcon icon="lucide:plus" />
                <span>新会话</span>
              </button>
            </div>
            <div class="service-grid">
              <button
                v-for="item in quickActions"
                :key="item.title"
                class="service-tile"
                type="button"
                @click="fillQuestion(item.query)"
              >
                <IconifyIcon :icon="item.icon" />
                <span>
                  <strong>{{ item.title }}</strong>
                  <small>{{ item.desc }}</small>
                </span>
              </button>
            </div>
          </section>

          <section class="tool-panel history-panel">
            <div class="panel-heading">
              <h2>最近咨询</h2>
              <a-spin v-if="conversationLoading || restoringConversation" size="small" />
            </div>
            <div v-if="!conversationLoading && !visibleConversations.length" class="history-empty">
              <a-empty description="暂无会话" />
            </div>
            <div v-else class="history-list">
              <div
                v-for="item in visibleConversations"
                :key="item.id"
                :class="['history-row', { active: item.id === conversationId }]"
              >
                <button class="history-main" type="button" @click="restoreConversation(item.id)">
                  <strong>{{ conversationLabel(item) }}</strong>
                  <span>{{ conversationPreview(item) }}</span>
                  <small>{{ item.message_count || 0 }} 条 · {{ formatTime(item.updated_at) }}</small>
                </button>
                <button
                  :aria-label="`删除 ${conversationLabel(item)}`"
                  class="history-delete"
                  :disabled="deletingConversationId === item.id"
                  :title="`删除 ${conversationLabel(item)}`"
                  type="button"
                  @click.stop="confirmDeleteConversation(item)"
                >
                  <a-spin v-if="deletingConversationId === item.id" size="small" />
                  <IconifyIcon v-else icon="lucide:trash-2" />
                </button>
              </div>
            </div>
          </section>
        </aside>

        <section class="conversation-panel">
          <header class="chat-titlebar">
            <div>
              <h1>和云维说明问题</h1>
              <p>{{ conversationTitle }}</p>
            </div>
            <button class="outline-button" data-testid="portal-submit-record-top" :disabled="creatingIssue || handoffPreparing" type="button" @click="transferToHuman()">
              <IconifyIcon icon="lucide:send" />
              <span>{{ creatingIssue || handoffPreparing ? '准备中' : '提交记录' }}</span>
            </button>
          </header>

          <div
            ref="chatBodyRef"
            class="chat-body"
            @pointerdown="handleChatUserIntent"
            @scroll.passive="handleChatScroll"
            @touchstart.passive="handleChatUserIntent"
            @wheel.passive="handleChatUserIntent"
          >
            <div
              v-for="item in chatMessages"
              :key="item.id"
              :class="['message-row', `message-${item.role}`]"
              :data-testid="`portal-chat-message-${item.role}`"
            >
              <div class="message-avatar">
                {{ item.role === 'user' ? '我' : item.role === 'system' ? '记' : '云' }}
              </div>
              <article :class="['message-bubble', { streaming: item.streaming }]">
                <div class="message-meta">
                  <strong>{{ item.role === 'user' ? '你' : item.role === 'system' ? '系统记录' : '云维' }}</strong>
                  <small>{{ item.createdAt }}</small>
                </div>
                <a-spin v-if="item.loading" />
                <div
                  v-if="item.role === 'assistant' || item.role === 'system'"
                  class="message-content"
                  v-html="renderMessageMarkdown(item.text)"
                ></div>
                <p v-else>{{ item.text }}</p>
                <span v-if="item.streaming" class="typing-cursor" aria-hidden="true">▍</span>

                <div v-if="showAnswerTags(item)" class="message-tags">
                  <a-tag v-if="item.intentLabel" color="cyan">{{ item.intentLabel }}</a-tag>
                  <a-tag v-if="item.riskLevel === 'high'" :color="riskColor(item.riskLevel)">高风险</a-tag>
                  <a-tag :color="item.needHuman ? 'red' : 'green'">
                    {{ item.needHuman ? '建议转人工' : '可自助处理' }}
                  </a-tag>
                </div>

                <div
                  v-if="showDecisionBox(item)"
                  class="decision-box"
                >
                  <div v-if="item.missingFields?.length" class="decision-row">
                    <span>还需要</span>
                    <a-tag v-for="field in item.missingFields" :key="field" color="orange">{{ field }}</a-tag>
                  </div>
                  <div v-if="item.clarificationQuestions?.length" class="clarify-list">
                    <span>快速补充</span>
                    <button
                      v-for="clarification in item.clarificationQuestions"
                      :key="clarification"
                      type="button"
                      @click="fillClarification(item.question || '', clarification)"
                    >
                      {{ clarification }}
                    </button>
                  </div>
                  <div v-if="item.handoffReasons?.length" class="decision-section">
                    <span>为什么建议转人工</span>
                    <ul>
                      <li v-for="reason in item.handoffReasons" :key="reason">{{ reason }}</li>
                    </ul>
                  </div>
                </div>

                <details v-if="item.references?.length && !item.streaming" class="source-disclosure">
                  <summary>查看参考来源</summary>
                  <article v-for="refItem in item.references.slice(0, 3)" :key="refItem.id" class="source-item">
                    <strong>{{ refItem.title }}</strong>
                    <span>{{ sourceTypeText(refItem.source_type) }}</span>
                    <p v-if="refItem.snippet">{{ refItem.snippet }}</p>
                  </article>
                </details>

                <div v-if="item.role === 'assistant' && !item.loading && !item.streaming" class="message-actions">
                  <button class="small-button" type="button" @click="fillQuestion(item.question || '')">
                    <IconifyIcon icon="lucide:rotate-ccw" />
                    <span>继续咨询</span>
                  </button>
                  <button
                    v-if="item.needHuman"
                    class="small-button danger"
                    data-testid="portal-create-issue-from-message"
                    :disabled="creatingIssue || handoffPreparing"
                    type="button"
                    @click="transferToHuman(item.question || '', item.issueDraft)"
                  >
                    <IconifyIcon icon="lucide:file-plus-2" />
                    <span>创建记录</span>
                  </button>
                </div>
              </article>
            </div>
          </div>

          <footer class="composer">
            <div class="suggestion-row">
              <button
                v-for="item in visibleSuggestions"
                :key="item.id"
                class="suggestion-chip"
                type="button"
                @click="fillQuestion(item.query || item.title)"
              >
                <span>{{ item.query || item.title }}</span>
                <small>{{ sourceTypeText(item.source_type) }}</small>
              </button>
            </div>
            <textarea
              ref="inputRef"
              v-model="question"
              class="composer-input"
              data-testid="portal-chat-input"
              placeholder="描述系统、账号、报错提示和影响范围"
              rows="3"
              @keydown.enter.exact.prevent="ask()"
            ></textarea>
            <div class="composer-actions">
              <button class="ghost-button" data-testid="portal-submit-record" :disabled="creatingIssue || handoffPreparing" type="button" @click="transferToHuman()">
                <IconifyIcon icon="lucide:file-plus-2" />
                <span>{{ creatingIssue || handoffPreparing ? '准备中' : '提交在线记录' }}</span>
              </button>
              <button class="primary-button" data-testid="portal-send" :disabled="loading" type="button" @click="ask()">
                <IconifyIcon icon="lucide:send-horizontal" />
                <span>{{ loading ? '分析中' : '发送' }}</span>
              </button>
            </div>
          </footer>
        </section>

        <aside class="right-rail">
          <section class="tool-panel next-step-panel">
            <div class="panel-heading">
              <h2>下一步</h2>
              <a-tag :color="latestAssistant?.needHuman ? 'red' : 'green'">
                {{ latestAssistant?.needHuman ? '需要协同' : '自助优先' }}
              </a-tag>
            </div>
            <div class="next-step-card">
              <span>{{ nextStepTitle(latestAssistant) }}</span>
              <button
                v-if="latestAssistant?.needHuman"
                class="primary-button compact full"
                :disabled="creatingIssue || handoffPreparing"
                type="button"
                @click="transferToHuman(latestAssistant?.question || '', latestAssistant?.issueDraft)"
              >
                <IconifyIcon icon="lucide:file-plus-2" />
                <span>提交在线记录</span>
              </button>
              <button v-else class="outline-button full" type="button" @click="startNewConversation">
                <IconifyIcon icon="lucide:message-square-plus" />
                <span>发起新咨询</span>
              </button>
            </div>
          </section>

          <section class="tool-panel issue-panel">
            <div class="panel-heading">
              <h2>进行中的记录</h2>
              <button class="text-button" type="button" @click="activeView = 'issues'">
                <IconifyIcon icon="lucide:list" />
                <span>全部</span>
              </button>
            </div>
            <a-empty v-if="!issuesLoading && !activeIssues.length" description="暂无进行中的记录" />
            <template v-else>
              <article v-for="item in activeIssues" :key="item.id" class="issue-card">
                <div class="issue-card-head">
                  <strong>#{{ item.id }} {{ item.title }}</strong>
                  <a-tag :color="statusOf(item.status).color">{{ statusOf(item.status).label }}</a-tag>
                </div>
                <p>{{ item.description }}</p>
                <div class="issue-meta">
                  <span>{{ item.updated_at }}</span>
                  <span>{{ item.category || 'general' }}</span>
                  <button v-if="item.attachment_url" class="attachment-link" type="button" @click="openAttachment(item.attachment_url)">
                    附件
                  </button>
                </div>
              </article>
            </template>
          </section>
        </aside>
      </section>

      <section v-show="activeView === 'issues'" class="issues-workspace">
        <header class="workspace-heading">
          <div>
            <h1>我的在线记录</h1>
            <p>查看处理进度、处理结果和满意度评价。</p>
          </div>
          <button class="primary-button compact" type="button" @click="activeView = 'chat'">
            <IconifyIcon icon="lucide:message-circle" />
            <span>咨询云维</span>
          </button>
        </header>

        <div class="issue-filter">
          <button
            v-for="item in statusOptions"
            :key="item.value"
            :class="['filter-button', { active: issueStatus === item.value }]"
            type="button"
            @click="issueStatus = item.value; loadIssues()"
          >
            {{ item.label }}
          </button>
          <a-tag color="blue">自动刷新中</a-tag>
        </div>

        <a-empty v-if="!issuesLoading && latestIssues.length === 0" description="暂无在线记录" />
        <div v-else class="issue-list">
          <article v-for="item in latestIssues" :key="item.id" class="issue-row" data-testid="portal-issue-row">
            <div class="issue-main">
              <div class="issue-title">
                <strong>#{{ item.id }} {{ item.title }}</strong>
                <a-tag :color="statusOf(item.status).color">{{ statusOf(item.status).label }}</a-tag>
                <a-tag :color="priorityOf(item.priority).color">优先级 {{ priorityOf(item.priority).label }}</a-tag>
              </div>
              <p>{{ item.description }}</p>
              <div class="issue-grid">
                <span>分类：{{ item.category || 'general' }}</span>
                <span>影响范围：{{ item.impact_scope || '未填写' }}</span>
                <span>联系方式：{{ item.contact_phone || '未填写' }}</span>
                <span>更新时间：{{ item.updated_at }}</span>
                <span>处理人：{{ item.handled_by_name || '待分派' }}</span>
                <span>处理耗时：{{ item.handling_minutes === null || item.handling_minutes === undefined ? '未完成' : `${item.handling_minutes} 分钟` }}</span>
              </div>
              <button v-if="item.attachment_url" class="attachment-link mt-3" type="button" @click="openAttachment(item.attachment_url)">
                <IconifyIcon icon="lucide:paperclip" />
                <span>查看附件：{{ attachmentName(item.attachment_url) }}</span>
              </button>
              <div v-if="item.solution" class="solution-box">
                <strong>处理结果</strong>
                <span>{{ item.solution }}</span>
              </div>
            </div>
            <aside class="progress-panel">
              <div class="progress-track">
                <span :class="{ active: ['submitted', 'accepted', 'processing', 'need_user_info', 'pending_visit', 'pending', 'handled', 'closed'].includes(item.status) }">已提交</span>
                <span :class="{ active: ['accepted', 'processing', 'need_user_info', 'pending_visit', 'handled', 'closed'].includes(item.status) }">已受理</span>
                <span :class="{ active: ['processing', 'need_user_info', 'pending_visit', 'handled', 'closed'].includes(item.status) }">处理中</span>
                <span :class="{ active: ['pending_visit', 'handled', 'closed'].includes(item.status) }">待回访</span>
                <span :class="{ active: item.status === 'closed' }">已关闭</span>
              </div>
              <div v-if="item.events?.length" class="event-list">
                <div v-for="event in item.events.slice(0, 3)" :key="`${item.id}-${event.created_at}-${event.event_type}`">
                  <strong>{{ event.content }}</strong>
                  <small>{{ event.operator_name || '系统' }} · {{ event.created_at }}</small>
                </div>
              </div>
              <button v-if="item.status === 'closed'" class="outline-button full" type="button" @click="openFeedback(item)">
                <IconifyIcon icon="lucide:star" />
                <span>{{ item.user_satisfaction_score ? '修改评价' : '评价处理结果' }}</span>
              </button>
            </aside>
          </article>
        </div>
      </section>
    </main>

    <a-modal
      v-model:open="handoffOpen"
      data-testid="portal-handoff-modal"
      title="确认在线记录"
      ok-text="确认提交"
      cancel-text="继续编辑"
      :confirm-loading="creatingIssue"
      @ok="submitHandoffIssue"
    >
      <a-form layout="vertical">
        <a-form-item label="问题标题">
          <a-input v-model:value="handoffForm.title" data-testid="portal-handoff-title" placeholder="请输入问题标题" />
        </a-form-item>
        <a-form-item label="问题描述">
          <a-textarea
            v-model:value="handoffForm.description"
            data-testid="portal-handoff-description"
            :rows="4"
            placeholder="请确认系统、账号、错误提示和影响范围"
          />
        </a-form-item>
        <div class="handoff-grid">
          <a-form-item label="分类">
            <a-select v-model:value="handoffForm.category">
              <a-select-option value="account">账号权限</a-select-option>
              <a-select-option value="network">网络/VPN</a-select-option>
              <a-select-option value="business">业务系统</a-select-option>
              <a-select-option value="database">数据库/中间件</a-select-option>
              <a-select-option value="general">其他</a-select-option>
            </a-select>
          </a-form-item>
          <a-form-item label="优先级">
            <a-select v-model:value="handoffForm.priority">
              <a-select-option value="low">低</a-select-option>
              <a-select-option value="medium">中</a-select-option>
              <a-select-option value="high">高</a-select-option>
            </a-select>
          </a-form-item>
        </div>
        <div class="handoff-grid">
          <a-form-item label="联系方式">
            <a-input v-model:value="handoffForm.contact_phone" placeholder="电话或企业 IM" />
          </a-form-item>
          <a-form-item label="影响范围">
            <a-input v-model:value="handoffForm.impact_scope" placeholder="本人/部门/全公司/生产影响" />
          </a-form-item>
        </div>
        <a-form-item label="错误日志或报错原文">
          <a-textarea
            v-model:value="handoffForm.log_excerpt"
            data-testid="portal-handoff-log"
            :rows="3"
            placeholder="可粘贴 error、exception、timeout 等关键日志"
          />
        </a-form-item>
        <a-form-item label="截图或日志附件">
          <div class="upload-row">
            <a-input
              v-model:value="handoffForm.attachment_url"
              placeholder="上传后自动填充，也可粘贴共享路径"
            />
            <a-upload :before-upload="beforeHandoffUpload" :show-upload-list="false" accept=".jpg,.jpeg,.png,.gif,.webp,.txt,.log,.pdf,.zip">
              <a-button :loading="handoffUploading">
                <IconifyIcon icon="lucide:upload" />
                上传
              </a-button>
            </a-upload>
          </div>
          <button v-if="handoffForm.attachment_url" class="attachment-link mt-2" type="button" @click="openAttachment(handoffForm.attachment_url)">
            <IconifyIcon icon="lucide:paperclip" />
            <span>预览/下载：{{ attachmentName(handoffForm.attachment_url) }}</span>
          </button>
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      v-model:open="feedbackOpen"
      title="满意度评价"
      ok-text="提交"
      :confirm-loading="feedbackSubmitting"
      @ok="submitFeedback"
    >
      <a-form layout="vertical">
        <a-form-item label="评分">
          <a-rate v-model:value="feedbackForm.satisfaction_score" />
          <span class="rating-text">{{ feedbackForm.satisfaction_score }} 分</span>
        </a-form-item>
        <a-form-item label="评价">
          <a-textarea
            v-model:value="feedbackForm.feedback"
            :rows="4"
            placeholder="说明处理结果是否满足预期"
          />
        </a-form-item>
      </a-form>
    </a-modal>
    </template>
  </div>
</template>

<style scoped>
.portal-page,
.portal-page * {
  box-sizing: border-box;
  letter-spacing: 0;
}

.portal-page {
  --accent: #0f766e;
  --accent-soft: #ccfbf1;
  --amber: #b45309;
  --border: #d8dee8;
  --danger: #be123c;
  --ink: #111827;
  --muted: #667085;
  --panel: #fff;
  --surface: #f5f7fa;
  background: var(--surface);
  color: var(--ink);
  min-height: 100vh;
  overflow-x: hidden;
}

button {
  font: inherit;
}

.portal-public {
  background:
    linear-gradient(180deg, #f8fafc 0%, #eef6f5 58%, #f5f7fa 100%);
  min-height: 100vh;
}

.public-topbar {
  align-items: center;
  display: flex;
  justify-content: space-between;
  min-height: 76px;
  padding: 16px clamp(18px, 4vw, 48px);
}

.public-shell {
  display: grid;
  gap: clamp(24px, 5vw, 56px);
  grid-template-columns: minmax(0, 1fr) minmax(340px, 420px);
  margin: 0 auto;
  max-width: 1180px;
  min-height: calc(100vh - 96px);
  padding: clamp(24px, 5vw, 64px) clamp(18px, 4vw, 48px) 48px;
}

.public-intro,
.public-login {
  min-width: 0;
}

.public-intro {
  align-content: center;
  display: grid;
  gap: 22px;
}

.public-kicker {
  align-items: center;
  background: #ecfdf5;
  border: 1px solid #99f6e4;
  border-radius: 8px;
  color: #115e59;
  display: inline-flex;
  font-weight: 700;
  gap: 8px;
  min-height: 36px;
  padding: 0 14px;
  width: fit-content;
}

.public-intro h1 {
  color: #101828;
  font-size: 50px;
  line-height: 1.04;
  max-width: 820px;
}

.public-intro h1 span {
  display: inline;
  white-space: nowrap;
}

.public-intro h1 span:not(:last-child)::after {
  content: '、';
}

.public-intro p {
  color: #475467;
  font-size: 17px;
  line-height: 1.8;
  max-width: 720px;
}

.public-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.public-service-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 6px;
  max-width: 760px;
}

.public-service-card {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  display: grid;
  gap: 8px;
  min-height: 136px;
  padding: 16px;
}

.public-service-card svg {
  color: var(--amber);
  font-size: 24px;
}

.public-service-card strong {
  color: #101828;
  font-size: 16px;
}

.public-service-card span {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.5;
}

.public-login {
  align-content: center;
  display: grid;
}

.login-panel {
  background: #fff;
  border: 1px solid #d9e2ec;
  border-radius: 8px;
  box-sizing: border-box;
  color: #111827;
  box-shadow: 0 24px 64px rgb(15 23 42 / 12%);
  display: flex;
  flex-direction: column;
  min-height: 624px;
  padding: 24px;
}

.login-panel :deep(.ant-form-item-label > label) {
  color: #111827;
  font-weight: 700;
}

.login-panel :deep(.ant-input),
.login-panel :deep(.ant-input-affix-wrapper) {
  background: #fff;
  border-color: #cbd5e1;
  color: #111827;
}

.login-panel :deep(.ant-input::placeholder),
.login-panel :deep(.ant-input-affix-wrapper input::placeholder) {
  color: #64748b;
}

.login-panel :deep(.ant-input-affix-wrapper input) {
  color: #111827;
}

.identity-segment,
.identity-actions {
  align-items: center;
  background: #f1f5f9;
  border: 1px solid rgb(15 23 42 / 9%);
  border-radius: 8px;
  display: flex;
  gap: 4px;
  padding: 4px;
}

.identity-segment {
  margin-bottom: 16px;
}

.identity-segment button,
.identity-actions button {
  align-items: center;
  background: transparent;
  border: 0;
  border-radius: 7px;
  color: #334155;
  cursor: pointer;
  display: inline-flex;
  gap: 7px;
  justify-content: center;
  min-height: 34px;
  padding: 0 12px;
}

.identity-segment button {
  flex: 1 1 0;
  font-weight: 700;
}

.identity-segment button.active,
.identity-actions button:hover {
  background: #fff;
  color: var(--accent);
  box-shadow: 0 1px 3px rgb(15 23 42 / 8%);
}

.identity-actions button.primary {
  background: var(--accent);
  color: #fff;
}

.panel-heading.vertical {
  align-items: flex-start;
  flex-direction: column;
  gap: 6px;
  min-height: 58px;
}

.panel-heading.vertical p {
  color: #344054;
  font-size: 13px;
  line-height: 1.6;
}

.demo-account {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  display: grid;
  gap: 4px;
  margin-top: 14px;
  min-height: 166px;
  padding: 12px;
}

.demo-account span,
.demo-account small {
  color: #344054;
  font-size: 13px;
}

.demo-account strong {
  color: #111827;
}

.demo-login-button {
  background: #ecfdf5;
  border: 1px solid #99f6e4;
  border-radius: 8px;
  color: #115e59;
  cursor: pointer;
  font-weight: 700;
  min-height: 34px;
  margin: 6px 0;
}

.portal-topbar {
  align-items: center;
  background: #fff;
  border-bottom: 1px solid var(--border);
  display: grid;
  gap: 16px;
  grid-template-columns: minmax(220px, 1fr) auto minmax(220px, 1fr);
  min-height: 72px;
  padding: 12px 24px;
  position: sticky;
  top: 0;
  z-index: 20;
}

.brand-block,
.user-block,
.portal-nav,
.nav-button,
.icon-button,
.text-button,
.outline-button,
.ghost-button,
.primary-button,
.small-button,
.stat-tile,
.model-tile,
.service-tile,
.issue-card-head,
.message-meta,
.message-tags,
.decision-row,
.composer-actions,
.workspace-heading,
.issue-title,
.issue-filter,
.progress-track {
  align-items: center;
  display: flex;
}

.brand-block {
  gap: 10px;
  min-width: 0;
}

.brand-mark,
.message-avatar {
  align-items: center;
  background: var(--accent);
  border-radius: 8px;
  color: #fff;
  display: inline-flex;
  flex: 0 0 auto;
  font-weight: 800;
  height: 40px;
  justify-content: center;
  width: 40px;
}

.brand-block strong,
.brand-block small,
.user-chip strong,
.user-chip small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.brand-block strong {
  font-size: 16px;
}

.brand-block small,
.user-chip small {
  color: var(--muted);
  font-size: 12px;
}

.portal-nav {
  background: #eef2f6;
  border: 1px solid #d9e2ec;
  border-radius: 8px;
  gap: 4px;
  justify-self: center;
  padding: 4px;
}

.nav-button,
.icon-button,
.text-button,
.outline-button,
.ghost-button,
.primary-button,
.small-button,
.filter-button,
.suggestion-chip,
.service-tile,
.history-row,
.history-delete,
.history-main {
  border: 0;
  border-radius: 8px;
  cursor: pointer;
}

.nav-button {
  background: transparent;
  color: #475467;
  gap: 6px;
  min-height: 36px;
  min-width: 92px;
  justify-content: center;
  padding: 0 14px;
}

.nav-button.active {
  background: #fff;
  color: var(--accent);
  font-weight: 700;
}

.user-block {
  gap: 8px;
  justify-self: end;
  min-width: 0;
}

.user-chip {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  max-width: 180px;
  min-width: 0;
  padding: 6px 10px;
}

.icon-button {
  background: #fff;
  border: 1px solid var(--border);
  color: #344054;
  height: 38px;
  justify-content: center;
  width: 38px;
}

.icon-button:hover,
.text-button:hover,
.outline-button:hover,
.ghost-button:hover,
.small-button:hover,
.filter-button:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.portal-content {
  display: grid;
  gap: 16px;
  margin: 0 auto;
  max-width: 1640px;
  min-height: calc(100vh - 72px);
  min-width: 0;
  overflow-x: hidden;
  padding: 12px 24px;
  width: 100%;
}

.status-band {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(5, minmax(0, 1fr));
}

.stat-tile,
.model-tile {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  gap: 12px;
  min-height: 64px;
  padding: 10px 12px;
}

.stat-tile svg,
.model-tile svg {
  color: var(--accent);
  flex: 0 0 auto;
  font-size: 24px;
}

.stat-tile strong,
.model-tile strong,
.stat-tile span,
.model-tile span {
  display: block;
}

.stat-tile strong,
.model-tile strong {
  font-size: 18px;
  line-height: 1.1;
}

.model-tile strong {
  font-size: 15px;
}

.stat-tile span,
.model-tile span {
  color: var(--muted);
  font-size: 12px;
  margin-top: 4px;
}

.portal-grid {
  display: grid;
  gap: 16px;
  grid-template-columns: 320px minmax(0, 1fr) 320px;
  height: calc(100vh - 168px);
  min-height: 640px;
  min-width: 0;
  overflow: hidden;
}

.left-rail,
.right-rail {
  gap: 16px;
  min-height: 0;
  min-width: 0;
}

.left-rail {
  align-content: start;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  overflow: hidden;
}

.right-rail {
  align-content: start;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.tool-panel,
.conversation-panel,
.issues-workspace {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  min-width: 0;
}

.tool-panel {
  overflow: hidden;
  padding: 14px;
}

.panel-heading {
  align-items: center;
  display: flex;
  gap: 10px;
  justify-content: space-between;
  margin-bottom: 12px;
}

h1,
h2,
p {
  margin: 0;
}

h1 {
  font-size: 22px;
  line-height: 1.25;
}

h2 {
  font-size: 15px;
  line-height: 1.3;
}

.text-button {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  color: #344054;
  gap: 6px;
  min-height: 34px;
  padding: 0 10px;
}

.service-panel,
.history-panel,
.issue-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
}

.service-panel {
  flex: 0 0 auto;
}

.history-panel {
  flex: 1 1 auto;
}

.service-grid {
  align-content: start;
  display: grid;
  gap: 8px;
  grid-auto-rows: minmax(0, 1fr);
  grid-template-columns: repeat(2, minmax(0, 1fr));
  min-width: 0;
}

.history-list {
  align-content: start;
  display: grid;
  flex: 1 1 auto;
  gap: 8px;
  grid-auto-rows: max-content;
  min-height: 0;
  overflow: auto;
  padding-right: 2px;
  scrollbar-width: thin;
}

.history-empty {
  align-items: center;
  display: grid;
  flex: 1 1 auto;
  justify-items: center;
  min-height: 120px;
}

.service-tile {
  align-items: flex-start;
  background: #fff;
  border: 1px solid #e2e8f0;
  color: var(--ink);
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 108px;
  padding: 10px;
  text-align: left;
  width: 100%;
}

.service-tile:hover,
.history-row:hover,
.issue-card:hover,
.issue-row:hover {
  border-color: #5eead4;
  box-shadow: 0 10px 24px rgb(15 23 42 / 8%);
}

.service-tile svg {
  color: var(--amber);
  flex: 0 0 auto;
  font-size: 20px;
}

.service-tile > span {
  min-width: 0;
  width: 100%;
}

.service-tile strong,
.service-tile small,
.history-row strong,
.history-row span,
.history-row small {
  display: block;
  min-width: 0;
  overflow: hidden;
  overflow-wrap: anywhere;
}

.service-tile small,
.history-row span,
.history-row small {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.45;
  margin-top: 4px;
}

.service-tile strong {
  font-size: 13px;
  line-height: 1.25;
}

.service-tile small {
  display: -webkit-box;
  font-size: 11px;
  line-height: 1.4;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.history-row {
  align-items: stretch;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  color: var(--ink);
  display: grid;
  gap: 4px;
  grid-template-columns: minmax(0, 1fr) 34px;
  min-height: 68px;
  padding: 4px;
  width: 100%;
}

.history-main {
  background: transparent;
  color: inherit;
  display: block;
  min-width: 0;
  padding: 6px;
  text-align: left;
}

.history-delete {
  align-self: start;
  background: transparent;
  color: #98a2b3;
  display: inline-grid;
  height: 30px;
  justify-content: center;
  margin-top: 2px;
  place-items: center;
  width: 30px;
}

.history-delete:hover {
  background: #fee2e2;
  color: #dc2626;
}

.history-delete:disabled {
  cursor: wait;
  opacity: 0.7;
}

.history-row strong {
  font-size: 13px;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-row span {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.history-row small {
  font-size: 11px;
}

.history-row.active {
  border-color: var(--accent);
  box-shadow: inset 3px 0 0 var(--accent);
}

.conversation-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

.chat-titlebar {
  align-items: center;
  border-bottom: 1px solid var(--border);
  display: flex;
  flex: 0 0 auto;
  gap: 16px;
  justify-content: space-between;
  padding: 12px 16px;
}

.chat-titlebar p,
.workspace-heading p {
  color: var(--muted);
  font-size: 13px;
  margin-top: 4px;
}

.outline-button,
.ghost-button,
.primary-button,
.small-button {
  gap: 8px;
  justify-content: center;
  min-height: 38px;
  padding: 0 14px;
}

.outline-button {
  background: #fff;
  border: 1px solid var(--border);
  color: #344054;
}

.outline-button.full {
  width: 100%;
}

.primary-button.full {
  width: 100%;
}

.primary-button.large,
.ghost-button.large,
.outline-button.compact {
  min-height: 44px;
}

.ghost-button {
  background: #f8fafc;
  border: 1px solid #d0d5dd;
  color: #344054;
}

.primary-button {
  background: var(--accent);
  color: #fff;
}

.primary-button.compact {
  min-width: 120px;
}

.small-button {
  background: #fff;
  border: 1px solid var(--border);
  color: #344054;
  min-height: 32px;
  padding: 0 10px;
}

.small-button.danger {
  border-color: #fecdd3;
  color: var(--danger);
}

.primary-button:disabled,
.ghost-button:disabled,
.outline-button:disabled,
.small-button:disabled,
.icon-button:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}

.chat-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 18px;
}

.message-row {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}

.message-user {
  flex-direction: row-reverse;
}

.message-bubble {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  max-width: min(760px, 88%);
  overflow-wrap: anywhere;
  padding: 12px 14px;
}

.message-bubble.streaming {
  min-width: min(320px, 88%);
}

.message-user .message-bubble {
  background: #0f766e;
  border-color: #0f766e;
  color: #fff;
}

.message-system .message-bubble {
  background: #fff7ed;
  border-color: #fed7aa;
}

.message-meta {
  gap: 8px;
  margin-bottom: 8px;
}

.message-meta small {
  color: var(--muted);
}

.message-user .message-meta small {
  color: rgb(255 255 255 / 72%);
}

.message-bubble p,
.message-content {
  line-height: 1.75;
  white-space: pre-wrap;
}

.message-content :deep(p),
.message-content :deep(ul),
.message-content :deep(ol),
.message-content :deep(h3) {
  margin: 0;
}

.message-content :deep(p + p),
.message-content :deep(p + ul),
.message-content :deep(p + ol),
.message-content :deep(ul + p),
.message-content :deep(ol + p),
.message-content :deep(h3 + p),
.message-content :deep(h3 + ul),
.message-content :deep(h3 + ol) {
  margin-top: 8px;
}

.message-content :deep(h3) {
  color: #0f172a;
  font-size: 14px;
  font-weight: 800;
  line-height: 1.4;
}

.message-content :deep(ul),
.message-content :deep(ol) {
  padding-left: 20px;
  white-space: normal;
}

.message-content :deep(ul) {
  list-style: disc;
}

.message-content :deep(ol) {
  list-style: decimal;
}

.message-content :deep(li) {
  margin-top: 4px;
}

.message-content :deep(strong) {
  font-weight: 800;
}

.message-content :deep(code) {
  background: #e2e8f0;
  border-radius: 4px;
  color: #0f172a;
  font-size: 0.92em;
  padding: 1px 5px;
}

.typing-cursor {
  color: #0f766e;
  display: inline-block;
  margin-top: 6px;
}

.message-tags,
.message-actions,
.decision-row {
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.decision-box,
.source-disclosure {
  background: #fff;
  border: 1px solid #dbeafe;
  border-radius: 8px;
  margin-top: 12px;
  padding: 10px;
}

.decision-section + .decision-section,
.decision-row + .decision-section,
.clarify-list + .decision-section {
  margin-top: 10px;
}

.decision-section > span,
.decision-row > span,
.clarify-list > span {
  color: var(--accent);
  display: block;
  font-size: 12px;
  font-weight: 800;
  margin-bottom: 6px;
}

.decision-section ul {
  margin: 0 0 0 18px;
  padding: 0;
}

.decision-section li {
  color: #344054;
  line-height: 1.55;
  margin-top: 4px;
}

.clarify-list {
  margin-top: 10px;
}

.clarify-list button {
  background: #ecfeff;
  border: 1px solid #bae6fd;
  border-radius: 8px;
  color: #0369a1;
  cursor: pointer;
  margin: 4px 6px 0 0;
  padding: 6px 9px;
}

.source-disclosure summary {
  color: #0369a1;
  cursor: pointer;
  font-size: 13px;
  font-weight: 800;
}

.source-item {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  display: grid;
  gap: 5px;
  margin-top: 8px;
  padding: 9px;
}

.source-item strong,
.source-item span,
.source-item p {
  min-width: 0;
  overflow-wrap: anywhere;
}

.source-item span {
  color: #0369a1;
  font-size: 12px;
  font-weight: 800;
}

.source-item p {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.5;
}

.composer {
  background: #fff;
  border-top: 1px solid var(--border);
  box-shadow: 0 -14px 34px rgb(15 23 42 / 8%);
  flex: 0 0 auto;
  padding: 14px 16px;
}

.suggestion-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
  padding-bottom: 2px;
}

.suggestion-chip {
  align-items: center;
  background: #f0fdfa;
  border: 1px solid #99f6e4;
  color: #115e59;
  display: inline-flex;
  flex: 1 1 220px;
  gap: 8px;
  justify-content: space-between;
  max-width: 100%;
  min-height: 32px;
  min-width: 0;
  overflow: hidden;
  padding: 0 10px;
}

.suggestion-chip span,
.suggestion-chip small {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.suggestion-chip span {
  flex: 1 1 auto;
}

.suggestion-chip small {
  color: #0f766e;
  flex: 0 0 auto;
  font-size: 11px;
  font-weight: 700;
}

.composer-input {
  border: 2px solid #0f766e;
  border-radius: 8px;
  color: var(--ink);
  display: block;
  font-size: 15px;
  line-height: 1.6;
  min-height: 104px;
  outline: none;
  padding: 12px;
  resize: vertical;
  width: 100%;
}

.composer-input:focus {
  border-color: var(--amber);
  box-shadow: 0 0 0 4px rgb(180 83 9 / 14%);
}

.composer-actions {
  gap: 10px;
  justify-content: flex-end;
  margin-top: 10px;
}

.issue-card {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  min-width: 0;
  overflow: hidden;
  padding: 10px;
}

.issue-card + .issue-card {
  margin-top: 8px;
}

.next-step-panel {
  flex: 0 0 auto;
}

.next-step-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  display: grid;
  gap: 12px;
  padding: 10px;
}

.next-step-card > span {
  color: #344054;
  line-height: 1.6;
}

.issue-card-head {
  display: grid;
  gap: 8px;
  grid-template-columns: minmax(0, 1fr) auto;
  justify-content: space-between;
  min-width: 0;
}

.issue-card-head strong,
.issue-title strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.issue-card-head :deep(.ant-tag) {
  max-width: 100%;
}

.issue-card p {
  color: #475467;
  display: -webkit-box;
  font-size: 13px;
  line-height: 1.55;
  margin-top: 8px;
  overflow: hidden;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.issue-meta {
  color: var(--muted);
  display: flex;
  flex-wrap: wrap;
  font-size: 12px;
  gap: 8px;
  margin-top: 8px;
  min-width: 0;
  overflow-wrap: anywhere;
}

.attachment-link {
  align-items: center;
  background: transparent;
  border: 0;
  color: var(--accent);
  cursor: pointer;
  display: inline-flex;
  font-size: 13px;
  gap: 6px;
  padding: 0;
  text-align: left;
  text-decoration: none;
}

.attachment-link:hover {
  color: var(--amber);
}

.handoff-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.upload-row {
  align-items: center;
  display: grid;
  gap: 8px;
  grid-template-columns: minmax(0, 1fr) auto;
}

.issues-workspace {
  padding: 18px;
}

.workspace-heading {
  gap: 16px;
  justify-content: space-between;
  margin-bottom: 16px;
}

.issue-filter {
  background: #eef2f6;
  border: 1px solid #d9e2ec;
  border-radius: 8px;
  gap: 6px;
  margin-bottom: 16px;
  padding: 6px;
  width: fit-content;
}

.filter-button {
  background: transparent;
  color: #475467;
  min-height: 34px;
  padding: 0 12px;
}

.filter-button.active {
  background: #fff;
  color: var(--accent);
  font-weight: 700;
}

.icon-button.refresh {
  background: #fff;
}

.issue-list {
  display: grid;
  gap: 12px;
}

.issue-row {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  display: grid;
  gap: 16px;
  grid-template-columns: minmax(0, 1fr) 320px;
  padding: 14px;
}

.issue-main,
.progress-panel {
  min-width: 0;
  overflow: hidden;
}

.issue-title {
  flex-wrap: wrap;
  gap: 8px;
}

.issue-main > p {
  color: #344054;
  line-height: 1.65;
  margin-top: 10px;
  white-space: pre-wrap;
}

.issue-grid {
  color: var(--muted);
  display: grid;
  font-size: 13px;
  gap: 6px 12px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 10px;
}

.solution-box {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 8px;
  color: #14532d;
  display: grid;
  gap: 4px;
  margin-top: 12px;
  padding: 10px;
}

.progress-panel {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
}

.progress-track {
  gap: 6px;
}

.progress-track span {
  background: #e5e7eb;
  border-radius: 8px;
  color: #475467;
  flex: 1;
  font-size: 12px;
  min-height: 28px;
  padding: 6px 4px;
  text-align: center;
}

.progress-track span.active {
  background: var(--accent-soft);
  color: #115e59;
  font-weight: 700;
}

.event-list {
  display: grid;
  gap: 8px;
}

.event-list div {
  border-left: 3px solid #99f6e4;
  padding-left: 8px;
}

.event-list strong,
.event-list small {
  display: block;
}

.event-list strong {
  color: #344054;
  font-size: 13px;
  line-height: 1.45;
}

.event-list small {
  color: var(--muted);
  font-size: 12px;
  margin-top: 3px;
}

.rating-text {
  color: var(--muted);
  margin-left: 10px;
}

@media (max-width: 1320px) {
  .portal-grid {
    grid-template-columns: 280px minmax(0, 1fr);
  }

  .right-rail {
    display: none;
  }
}

@media (max-width: 980px) {
  .public-shell {
    grid-template-columns: 1fr;
    min-height: auto;
  }

  .public-login {
    align-content: start;
  }

  .public-intro h1 {
    font-size: 44px;
    line-height: 1.1;
  }

  .login-panel {
    min-height: auto;
  }

  .portal-topbar {
    grid-template-columns: 1fr;
    position: static;
  }

  .portal-nav,
  .user-block {
    justify-self: stretch;
  }

  .user-block {
    justify-content: space-between;
  }

  .identity-actions {
    order: 3;
    width: 100%;
  }

  .identity-actions button {
    flex: 1 1 0;
  }

  .status-band {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .model-tile {
    grid-column: 1 / -1;
  }

  .portal-grid {
    grid-template-columns: 1fr;
    height: auto;
    min-height: 0;
    overflow: visible;
  }

  .conversation-panel {
    height: min(720px, calc(100vh - 180px));
    min-height: 560px;
  }

  .left-rail {
    grid-template-rows: none;
    order: 2;
    overflow: visible;
  }

  .history-list {
    max-height: none;
    overflow: visible;
    padding-right: 0;
  }

  .issue-row {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .public-topbar {
    align-items: stretch;
    flex-direction: column;
    gap: 12px;
  }

  .public-service-grid {
    grid-template-columns: 1fr;
  }

  .public-intro h1 {
    font-size: 34px;
    line-height: 1.12;
  }

  .portal-content,
  .portal-topbar {
    padding-left: 12px;
    padding-right: 12px;
  }

  .identity-actions button span {
    display: none;
  }

  .status-band {
    grid-template-columns: 1fr;
  }

  .service-grid {
    grid-template-columns: 1fr;
  }

  .portal-nav,
  .composer-actions,
  .workspace-heading,
  .chat-titlebar {
    align-items: stretch;
    flex-direction: column;
  }

  .nav-button,
  .primary-button,
  .ghost-button,
  .outline-button {
    width: 100%;
  }

  .message-row,
  .message-user {
    flex-direction: column;
  }

  .message-bubble {
    max-width: 100%;
  }

  .issue-grid {
    grid-template-columns: 1fr;
  }

  .handoff-grid,
  .upload-row {
    grid-template-columns: 1fr;
  }
}
</style>
