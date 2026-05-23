<script lang="ts" setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { IconifyIcon } from '@vben/icons';
import { useUserStore } from '@vben/stores';

import { message } from 'ant-design-vue';

import {
  askQuestion,
  createIssue,
  draftIssue,
  feedbackIssue,
  getLlmStatus,
  getQaConversation,
  getStats,
  listIssues,
  listQaConversations,
  suggestQuestions,
  uploadIssueAttachment,
} from '#/api/ops';
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
const authStore = useAuthStore();
const userStore = useUserStore();

const activeView = ref<PortalView>('chat');
const question = ref('');
const currentQuestion = ref('');
const conversationId = ref<number | null>(null);
const conversationTitle = ref('新会话');
const loading = ref(false);
const creatingIssue = ref(false);
const conversationLoading = ref(false);
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
let suggestTimer: ReturnType<typeof setTimeout> | undefined;

const quickActions = [
  {
    desc: '登录失败、冻结、解冻、密码与权限申请',
    icon: 'lucide:key-round',
    query: '账号被冻结了，怎么恢复使用？',
    title: '账号与权限',
  },
  {
    desc: 'VPN、证书、远程办公和网络连通性',
    icon: 'lucide:wifi',
    query: 'VPN 无法连接，应该怎么排查？',
    title: '网络与 VPN',
  },
  {
    desc: '业务系统报错、访问慢、页面异常',
    icon: 'lucide:monitor-alert',
    query: '业务系统页面报错，怎么判断影响范围？',
    title: '业务系统',
  },
  {
    desc: '数据库连接、超时、权限和中间件异常',
    icon: 'lucide:database',
    query: '数据库连接失败，怎么判断影响范围？',
    title: '数据库/中间件',
  },
];

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
const activeIssues = computed(() => issues.value.filter((item) => item.status !== 'closed').slice(0, 4));
const latestIssues = computed(() => issues.value.slice(0, 8));

async function scrollToBottom() {
  await nextTick();
  if (chatBodyRef.value) {
    chatBodyRef.value.scrollTop = chatBodyRef.value.scrollHeight;
  }
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

function riskText(level = '') {
  const labels: Record<string, string> = { high: '高风险', low: '低风险', medium: '中风险' };
  return labels[level] || '未识别';
}

function conversationPreview(item: QaConversation) {
  return item.last_message || item.title || `会话 #${item.id}`;
}

function attachmentName(url = '') {
  if (!url) return '';
  const clean = url.split('?')[0] || '';
  return clean.split('/').filter(Boolean).pop() || url;
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
    await scrollToBottom();
  } catch (error: any) {
    message.error(error?.message || '会话恢复失败');
  } finally {
    restoringConversation.value = false;
  }
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
    text: '正在检索企业知识库并整理建议...',
  });
  await scrollToBottom();

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
      assistantMessage.loading = false;
      assistantMessage.missingFields = result.missing_fields || [];
      assistantMessage.needHuman = result.need_human;
      assistantMessage.nextActions = result.next_actions || [];
      assistantMessage.question = rawQuestion;
      assistantMessage.rag = result.rag;
      assistantMessage.references = result.references || [];
      assistantMessage.riskLevel = result.risk_level;
      assistantMessage.status = result.model_status;
      assistantMessage.text = result.answer;
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

async function logout() {
  await authStore.logout(false);
}

watch(
  question,
  (value) => {
    if (suggestTimer) clearTimeout(suggestTimer);
    suggestTimer = setTimeout(() => loadSuggestions(value), 180);
  },
);

onMounted(async () => {
  await Promise.all([loadStats(), loadLlmStatus(), loadSuggestions(''), loadConversations(), loadIssues()]);
  await nextTick();
  inputRef.value?.focus();
});
</script>

<template>
  <div class="portal-page">
    <header class="portal-topbar">
      <div class="brand-block">
        <span class="brand-mark">云</span>
        <div>
          <strong>云维服务门户</strong>
          <small>企业运维数字员工</small>
        </div>
      </div>

      <nav class="portal-nav" aria-label="服务视图">
        <button :class="['nav-button', { active: activeView === 'chat' }]" type="button" @click="activeView = 'chat'">
          <IconifyIcon icon="lucide:message-circle" />
          <span>咨询</span>
        </button>
        <button :class="['nav-button', { active: activeView === 'issues' }]" type="button" @click="activeView = 'issues'">
          <IconifyIcon icon="lucide:clipboard-list" />
          <span>记录</span>
        </button>
      </nav>

      <div class="user-block">
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
          </section>

          <section class="tool-panel history-panel">
            <div class="panel-heading">
              <h2>最近咨询</h2>
              <a-spin v-if="conversationLoading || restoringConversation" size="small" />
            </div>
            <a-empty v-if="!conversationLoading && !visibleConversations.length" description="暂无会话" />
            <template v-else>
              <button
                v-for="item in visibleConversations"
                :key="item.id"
                :class="['history-row', { active: item.id === conversationId }]"
                type="button"
                @click="restoreConversation(item.id)"
              >
                <strong>{{ item.title || `会话 #${item.id}` }}</strong>
                <span>{{ conversationPreview(item) }}</span>
                <small>{{ item.message_count || 0 }} 条 · {{ formatTime(item.updated_at) }}</small>
              </button>
            </template>
          </section>
        </aside>

        <section class="conversation-panel">
          <header class="chat-titlebar">
            <div>
              <h1>和云维说明问题</h1>
              <p>{{ conversationTitle }}</p>
            </div>
            <button class="outline-button" :disabled="creatingIssue || handoffPreparing" type="button" @click="transferToHuman()">
              <IconifyIcon icon="lucide:send" />
              <span>{{ creatingIssue || handoffPreparing ? '准备中' : '提交记录' }}</span>
            </button>
          </header>

          <div ref="chatBodyRef" class="chat-body">
            <div
              v-for="item in chatMessages"
              :key="item.id"
              :class="['message-row', `message-${item.role}`]"
            >
              <div class="message-avatar">
                {{ item.role === 'user' ? '我' : item.role === 'system' ? '记' : '云' }}
              </div>
              <article class="message-bubble">
                <div class="message-meta">
                  <strong>{{ item.role === 'user' ? '你' : item.role === 'system' ? '系统记录' : '云维' }}</strong>
                  <small>{{ item.createdAt }}</small>
                </div>
                <a-spin v-if="item.loading" />
                <p>{{ item.text }}</p>

                <div v-if="item.role === 'assistant' && !item.loading" class="message-tags">
                  <a-tag v-if="item.intentLabel" color="cyan">{{ item.intentLabel }}</a-tag>
                  <a-tag v-if="item.riskLevel" :color="riskColor(item.riskLevel)">风险：{{ riskText(item.riskLevel) }}</a-tag>
                  <a-tag v-if="item.confidence !== undefined" color="geekblue">
                    置信度 {{ Math.round((item.confidence || 0) * 100) }}%
                  </a-tag>
                  <a-tag :color="item.needHuman ? 'red' : 'green'">
                    {{ item.needHuman ? '建议转人工' : '可自助处理' }}
                  </a-tag>
                </div>

                <div
                  v-if="item.role === 'assistant' && !item.loading && (item.missingFields?.length || item.handoffReasons?.length || item.nextActions?.length)"
                  class="decision-box"
                >
                  <div v-if="item.automationSummary?.length" class="decision-section">
                    <span>研判摘要</span>
                    <ul>
                      <li v-for="summary in item.automationSummary" :key="summary">{{ summary }}</li>
                    </ul>
                  </div>
                  <div v-if="item.missingFields?.length" class="decision-row">
                    <span>待补充</span>
                    <a-tag v-for="field in item.missingFields" :key="field" color="orange">{{ field }}</a-tag>
                  </div>
                  <div v-if="item.clarificationQuestions?.length" class="clarify-list">
                    <span>补充项</span>
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
                    <span>转人工依据</span>
                    <ul>
                      <li v-for="reason in item.handoffReasons" :key="reason">{{ reason }}</li>
                    </ul>
                  </div>
                </div>

                <div v-if="item.references?.length" class="reference-list">
                  <div class="reference-title">
                    <span>引用来源</span>
                    <small v-if="item.rag?.strategy">{{ item.rag.strategy }}</small>
                  </div>
                  <article v-for="refItem in item.references" :key="refItem.id" class="reference-item">
                    <div>
                      <strong>{{ refItem.title }}</strong>
                      <a-tag color="geekblue">{{ Math.round((refItem.score || 0) * 100) }}%</a-tag>
                    </div>
                    <p v-if="refItem.snippet">{{ refItem.snippet }}</p>
                  </article>
                </div>

                <div v-if="item.role === 'assistant' && !item.loading" class="message-actions">
                  <button class="small-button" type="button" @click="fillQuestion(item.question || '')">
                    <IconifyIcon icon="lucide:rotate-ccw" />
                    <span>继续咨询</span>
                  </button>
                  <button
                    v-if="item.needHuman"
                    class="small-button danger"
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
                {{ item.query || item.title }}
              </button>
            </div>
            <textarea
              ref="inputRef"
              v-model="question"
              class="composer-input"
              placeholder="描述系统、账号、报错提示和影响范围"
              rows="3"
              @keydown.enter.exact.prevent="ask()"
            ></textarea>
            <div class="composer-actions">
              <button class="ghost-button" :disabled="creatingIssue || handoffPreparing" type="button" @click="transferToHuman()">
                <IconifyIcon icon="lucide:file-plus-2" />
                <span>{{ creatingIssue || handoffPreparing ? '准备中' : '提交在线记录' }}</span>
              </button>
              <button class="primary-button" :disabled="loading" type="button" @click="ask()">
                <IconifyIcon icon="lucide:send-horizontal" />
                <span>{{ loading ? '分析中' : '发送' }}</span>
              </button>
            </div>
          </footer>
        </section>

        <aside class="right-rail">
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
                  <a v-if="item.attachment_url" class="attachment-link" :href="item.attachment_url" rel="noopener noreferrer" target="_blank">
                    附件
                  </a>
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
          <button class="icon-button refresh" :disabled="issuesLoading" type="button" @click="loadIssues">
            <IconifyIcon icon="lucide:refresh-cw" />
          </button>
        </div>

        <a-empty v-if="!issuesLoading && latestIssues.length === 0" description="暂无在线记录" />
        <div v-else class="issue-list">
          <article v-for="item in latestIssues" :key="item.id" class="issue-row">
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
              <a v-if="item.attachment_url" class="attachment-link mt-3" :href="item.attachment_url" rel="noopener noreferrer" target="_blank">
                <IconifyIcon icon="lucide:paperclip" />
                <span>查看附件：{{ attachmentName(item.attachment_url) }}</span>
              </a>
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
      title="确认在线记录"
      ok-text="确认提交"
      cancel-text="继续编辑"
      :confirm-loading="creatingIssue"
      @ok="submitHandoffIssue"
    >
      <a-form layout="vertical">
        <a-form-item label="问题标题">
          <a-input v-model:value="handoffForm.title" placeholder="请输入问题标题" />
        </a-form-item>
        <a-form-item label="问题描述">
          <a-textarea
            v-model:value="handoffForm.description"
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
          <a v-if="handoffForm.attachment_url" class="attachment-link mt-2" :href="handoffForm.attachment_url" rel="noopener noreferrer" target="_blank">
            <IconifyIcon icon="lucide:paperclip" />
            <span>预览/下载：{{ attachmentName(handoffForm.attachment_url) }}</span>
          </a>
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
.history-row {
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
  min-width: 0;
  overflow-x: hidden;
  padding: 16px 24px 24px;
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
  min-height: 76px;
  padding: 14px;
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
  font-size: 22px;
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
  grid-template-columns: 300px minmax(0, 1fr) 320px;
  min-height: calc(100vh - 180px);
  min-width: 0;
  overflow: hidden;
}

.left-rail,
.right-rail {
  display: grid;
  gap: 16px;
  min-width: 0;
}

.left-rail {
  align-content: start;
}

.right-rail {
  align-content: start;
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
  display: grid;
  gap: 8px;
}

.service-tile {
  align-items: flex-start;
  background: #fff;
  border: 1px solid #e2e8f0;
  color: var(--ink);
  display: flex;
  gap: 10px;
  min-height: 78px;
  padding: 12px;
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
  font-size: 22px;
  margin-top: 2px;
}

.service-tile strong,
.service-tile small,
.history-row strong,
.history-row span,
.history-row small {
  display: block;
  min-width: 0;
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

.history-row {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  color: var(--ink);
  display: block;
  min-height: 76px;
  padding: 10px;
  text-align: left;
  width: 100%;
}

.history-row.active {
  border-color: var(--accent);
  box-shadow: inset 3px 0 0 var(--accent);
}

.conversation-panel {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 180px);
  overflow: hidden;
}

.chat-titlebar {
  align-items: center;
  border-bottom: 1px solid var(--border);
  display: flex;
  gap: 16px;
  justify-content: space-between;
  padding: 16px 18px;
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

.message-bubble p {
  line-height: 1.75;
  white-space: pre-wrap;
}

.message-tags,
.message-actions,
.decision-row {
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.decision-box,
.reference-list {
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
.clarify-list > span,
.reference-title span {
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

.reference-title {
  align-items: center;
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}

.reference-title small {
  color: var(--muted);
}

.reference-item {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 9px;
}

.reference-item + .reference-item {
  margin-top: 8px;
}

.reference-item div {
  align-items: center;
  display: flex;
  gap: 8px;
  justify-content: space-between;
}

.reference-item p {
  color: #475467;
  font-size: 13px;
  margin-top: 6px;
}

.composer {
  background: #fff;
  border-top: 1px solid var(--border);
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
  background: #f0fdfa;
  border: 1px solid #99f6e4;
  color: #115e59;
  flex: 1 1 220px;
  max-width: 100%;
  min-height: 32px;
  min-width: 0;
  overflow: hidden;
  padding: 0 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-input {
  border: 2px solid #0f766e;
  border-radius: 8px;
  color: var(--ink);
  display: block;
  font-size: 15px;
  line-height: 1.6;
  min-height: 96px;
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
  color: var(--accent);
  display: inline-flex;
  font-size: 13px;
  gap: 6px;
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

  .status-band {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .model-tile {
    grid-column: 1 / -1;
  }

  .portal-grid {
    grid-template-columns: 1fr;
  }

  .conversation-panel {
    min-height: 720px;
  }

  .left-rail {
    order: 2;
  }

  .issue-row {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .portal-content,
  .portal-topbar {
    padding-left: 12px;
    padding-right: 12px;
  }

  .status-band {
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
