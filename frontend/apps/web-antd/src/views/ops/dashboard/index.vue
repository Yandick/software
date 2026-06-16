<script lang="ts" setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue';

import { message } from 'ant-design-vue';

import {
  askQuestion,
  createIssue,
  draftIssue,
  getLlmStatus,
  getQaConversation,
  getStats,
  listQaConversations,
  suggestQuestions,
} from '#/api/ops';

type ChatRole = 'assistant' | 'system' | 'user';

interface ChatMessage {
  agentMode?: string;
  agentTrace?: any[];
  answer?: string;
  automationSummary?: string[];
  clarificationQuestions?: string[];
  confidence?: number;
  createdAt: string;
  handoffReasons?: string[];
  id: string;
  intentLabel?: string;
  loading?: boolean;
  missingFields?: string[];
  needHuman?: boolean;
  issueDraft?: Record<string, any>;
  nextActions?: any[];
  question?: string;
  rag?: any;
  reasoningAvailable?: boolean;
  reasoningEnabled?: boolean;
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

const question = ref('');
const currentQuestion = ref('');
const conversationId = ref<number | null>(null);
const conversationTitle = ref('新会话');
const needHuman = ref(false);
const modelStatus = ref('');
const loading = ref(false);
const creatingIssue = ref(false);
const conversationLoading = ref(false);
const restoringConversation = ref(false);
const currentIssueDraft = ref<Record<string, any> | null>(null);
const stats = ref<any>({});
const llmStatus = ref<any>({});
const suggestions = ref<any[]>([]);
const conversations = ref<QaConversation[]>([]);
const suggestLoading = ref(false);
const enableThinking = ref(false);
const chatBodyRef = ref<HTMLElement | null>(null);
const inputRef = ref<HTMLTextAreaElement | null>(null);
let suggestTimer: ReturnType<typeof setTimeout> | undefined;

const quickActions = [
  { query: '账号被冻结了，怎么恢复使用？', title: '账号冻结', desc: '核验原因、解冻路径与人工边界' },
  { query: 'VPN 无法连接，应该怎么排查？', title: 'VPN 故障', desc: '网络、证书、客户端版本核查' },
  { query: '我需要申请业务系统权限，需要准备哪些信息？', title: '权限申请', desc: '申请字段、审批链路与审计要求' },
  { query: '数据库连接失败，怎么判断影响范围？', title: '数据库连接', desc: '连接配置、日志信息与影响范围' },
];

function createWelcomeMessage(): ChatMessage {
  return {
    createdAt: new Date().toLocaleTimeString(),
    id: 'welcome',
    role: 'assistant',
    text: '您好，我是云维，企业运维数字员工。请描述涉及系统、账号、错误提示和影响范围。我会基于企业知识库给出处置建议；需要人工协同时，将整理在线记录并进入运维处理流程。',
  };
}

const chatMessages = ref<ChatMessage[]>([createWelcomeMessage()]);

const llmModeText = computed(() => {
  if (!llmStatus.value.employee_name) return '检测中';
  if (llmStatus.value.ready) return `${llmStatus.value.vllm_model_name} 已接入`;
  return '智能服务暂未接入';
});

const visibleSuggestions = computed(() => suggestions.value.slice(0, 6));
const visibleConversations = computed(() => conversations.value.slice(0, 8));

const thinkingModeText = computed(() => {
  if (enableThinking.value) return '增强研判';
  return '快速问答';
});

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

function conversationPreview(item: QaConversation) {
  return item.last_message || item.title || `数字员工会话 #${item.id}`;
}

function riskColor(level = '') {
  const colors: Record<string, string> = { high: 'red', low: 'green', medium: 'orange' };
  return colors[level] || 'default';
}

function riskText(level = '') {
  const labels: Record<string, string> = { high: '高风险', low: '低风险', medium: '中风险' };
  return labels[level] || '未识别';
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

async function loadSuggestions(keyword = question.value) {
  suggestLoading.value = true;
  try {
    suggestions.value = await suggestQuestions(keyword.trim());
  } catch {
    suggestions.value = quickActions.map((item, index) => ({
      id: `local-${index}`,
      query: item.query,
      source_type: 'local',
      tags: [item.title],
      title: item.title,
    }));
  } finally {
    suggestLoading.value = false;
  }
}

function fillQuestion(text: string) {
  question.value = text;
}

function fillClarification(baseQuestion = '', clarification = '') {
  question.value = `${baseQuestion}\n补充信息：${clarification}`.trim();
  inputRef.value?.focus();
}

function startNewConversation() {
  conversationId.value = null;
  conversationTitle.value = '新会话';
  currentQuestion.value = '';
  currentIssueDraft.value = null;
  needHuman.value = false;
  modelStatus.value = '';
  chatMessages.value = [createWelcomeMessage()];
  question.value = '';
  message.success('已开启新会话');
  void nextTick(() => inputRef.value?.focus());
}

async function restoreConversation(id: number) {
  restoringConversation.value = true;
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
        answer: item.role === 'assistant' ? item.content : undefined,
        agentMode: metadata.agent?.mode,
        agentTrace: metadata.agent?.trace || [],
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
        reasoningAvailable: !!metadata.reasoning_available,
        reasoningEnabled: !!metadata.reasoning_enabled,
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
    modelStatus.value = lastAssistant?.status || '';
    chatMessages.value = restored.length ? restored : [createWelcomeMessage()];
    message.success(`已恢复会话 #${id}`);
    await scrollToBottom();
  } catch (error: any) {
    message.error(error?.message || '恢复会话失败');
  } finally {
    restoringConversation.value = false;
  }
}

async function ask(text = question.value) {
  const rawQuestion = text.trim();
  if (!rawQuestion) {
    await loadSuggestions('');
    message.info('请描述运维诉求，或选择左侧常见场景');
    return;
  }

  currentQuestion.value = rawQuestion;
  question.value = '';
  loading.value = true;
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
    text: '云维正在检索企业知识库、核对风险边界并整理处置建议...',
  });
  await scrollToBottom();

  try {
    const result = await askQuestion(rawQuestion, enableThinking.value, conversationId.value);
    conversationId.value = result.conversation_id || conversationId.value;
    conversationTitle.value = conversationTitle.value === '新会话' ? rawQuestion.slice(0, 40) : conversationTitle.value;
    const assistantMessage = chatMessages.value.find((item) => item.id === assistantMessageId);
    if (assistantMessage) {
      assistantMessage.answer = result.answer;
      assistantMessage.agentMode = result.agent?.mode;
      assistantMessage.agentTrace = result.agent?.trace || [];
      assistantMessage.automationSummary = result.automation_summary || [];
      assistantMessage.clarificationQuestions = result.clarification_questions || [];
      assistantMessage.confidence = result.confidence;
      assistantMessage.handoffReasons = result.handoff_reasons || [];
      assistantMessage.intentLabel = result.intent_label;
      assistantMessage.loading = false;
      assistantMessage.missingFields = result.missing_fields || [];
      assistantMessage.needHuman = result.need_human;
      assistantMessage.issueDraft = result.issue_draft;
      assistantMessage.nextActions = result.next_actions || [];
      assistantMessage.question = rawQuestion;
      assistantMessage.rag = result.rag;
      assistantMessage.reasoningAvailable = !!result.reasoning_available;
      assistantMessage.reasoningEnabled = !!result.reasoning_enabled;
      assistantMessage.references = result.references || [];
      assistantMessage.riskLevel = result.risk_level;
      assistantMessage.status = result.model_status;
      assistantMessage.text = result.answer;
    }
    needHuman.value = result.need_human;
    currentIssueDraft.value = result.issue_draft || null;
    modelStatus.value = result.model_status;
    if (result.employee) {
      llmStatus.value = {
        ...llmStatus.value,
        employee_name: result.employee.name,
        employee_role: result.employee.role,
        mode: 'llm',
        ready: true,
      };
    }
    await Promise.all([loadStats(), loadConversations()]);
  } catch (error: any) {
    const assistantMessage = chatMessages.value.find((item) => item.id === assistantMessageId);
    if (assistantMessage) {
      assistantMessage.loading = false;
      assistantMessage.needHuman = true;
      assistantMessage.question = rawQuestion;
      assistantMessage.status = 'unavailable';
      assistantMessage.text = '智能服务暂时不可用。可先创建在线记录，由运维人员继续处理。';
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
    message.warning('请先填写问题描述，再创建在线记录');
    return;
  }
  creatingIssue.value = true;
  try {
    const draft = reusableDraft || (await draftIssue(issueText));
    await createIssue({
      ...draft,
      description: draft.description || issueText,
      priority: needHuman.value ? 'high' : draft.priority || 'medium',
      title: draft.title || issueText.slice(0, 40),
    });
    const missingText = draft.missing_fields?.length
      ? `\n仍建议补充：${draft.missing_fields.join('、')}。`
      : '';
    chatMessages.value.push({
      createdAt: nowText(),
      id: `s-${Date.now()}`,
      role: 'system',
      text: `已根据智能研判结果创建在线记录：分类 ${draft.category}、优先级 ${draft.priority}、影响范围 ${draft.impact_scope || '未识别'}。${missingText}\n请在“在线记录”页面跟踪处理进度；运维处理完成后将进入回访，符合条件的处理结果会沉淀为知识候选。`,
    });
    message.success('在线记录已创建，可在“在线记录”页面跟进处理');
    await Promise.all([loadStats(), scrollToBottom()]);
  } finally {
    creatingIssue.value = false;
  }
}

watch(
  question,
  (value) => {
    if (suggestTimer) clearTimeout(suggestTimer);
    suggestTimer = setTimeout(() => loadSuggestions(value), 180);
  },
);

onMounted(async () => {
  await Promise.all([loadStats(), loadLlmStatus(), loadSuggestions(''), loadConversations()]);
  await nextTick();
  inputRef.value?.focus();
});
</script>

<template>
  <div class="ops-chat-page">
    <section class="chat-shell">
      <aside class="chat-sidebar">
        <div class="brand-card">
          <div class="avatar">云</div>
          <div>
            <div class="eyebrow">OPS AI EMPLOYEE</div>
            <h1>运维服务台</h1>
            <p>提交问题现象，系统将结合知识库、风险边界和历史记录给出处置建议。</p>
          </div>
        </div>

        <div class="status-card">
          <div class="status-row">
            <span>模型状态</span>
            <a-tag :color="llmStatus.ready ? 'green' : 'red'">{{ llmModeText }}</a-tag>
          </div>
          <div class="status-row">
            <span>会话上下文</span>
            <a-tag :color="conversationId ? 'green' : 'default'">
              {{ conversationId ? `连续会话 #${conversationId}` : '新会话' }}
            </a-tag>
          </div>
          <div class="status-row">
            <span>回答模式</span>
            <a-tag :color="enableThinking ? 'gold' : 'blue'">{{ thinkingModeText }}</a-tag>
          </div>
          <div class="mt-3 flex items-center justify-between rounded-2xl bg-white/70 px-3 py-2">
            <span class="text-sm text-slate-600">启用增强研判</span>
            <a-switch v-model:checked="enableThinking" />
          </div>
        </div>

        <div class="session-card">
          <div class="mb-3 flex items-center justify-between gap-2">
            <div>
              <h2>历史会话</h2>
              <p>查看已保存的咨询上下文，继续补充信息或创建在线记录。</p>
            </div>
            <a-button size="small" @click="startNewConversation">开启新会话</a-button>
          </div>
          <a-spin v-if="conversationLoading || restoringConversation" />
          <div v-else-if="!visibleConversations.length" class="empty-session">
            暂无历史会话，提交第一条问题后会自动保存。
          </div>
          <template v-else>
            <button
              v-for="item in visibleConversations"
              :key="item.id"
              :class="['session-button', { active: item.id === conversationId }]"
              type="button"
              @click="restoreConversation(item.id)"
            >
              <span>{{ item.title || `会话 #${item.id}` }}</span>
              <small>{{ conversationPreview(item) }}</small>
              <em>{{ item.message_count || 0 }} 条 · {{ formatTime(item.updated_at) }}</em>
            </button>
          </template>
        </div>

        <div class="metrics-grid">
          <div>
            <strong>{{ stats.total_qa || 0 }}</strong>
            <span>问答</span>
          </div>
          <div>
            <strong>{{ Math.round((stats.self_solved_rate || 0) * 100) }}%</strong>
            <span>自助</span>
          </div>
          <div>
            <strong>{{ stats.issues || 0 }}</strong>
            <span>记录</span>
          </div>
          <div>
            <strong>{{ stats.knowledge || 0 }}</strong>
            <span>知识</span>
          </div>
        </div>

        <div class="suggest-card">
          <div class="mb-3 flex items-center justify-between">
            <div>
              <h2>常见服务场景</h2>
              <p>可选择典型问题，也可以直接提交新的运维诉求。</p>
            </div>
            <a-tag v-if="suggestLoading">匹配中</a-tag>
          </div>
          <button
            v-for="item in visibleSuggestions"
            :key="item.id"
            class="suggest-button"
            type="button"
            @click="fillQuestion(item.query || item.title)"
          >
            <span>{{ item.query || item.title }}</span>
            <small>{{ item.title }} · {{ item.source_type }}</small>
          </button>
          <button
            v-for="item in quickActions"
            :key="item.title"
            class="quick-button"
            type="button"
            @click="ask(item.query)"
          >
            <strong>{{ item.title }}</strong>
            <span>{{ item.desc }}</span>
          </button>
        </div>
      </aside>

      <main class="chat-main">
        <header class="chat-header">
          <div>
            <div class="eyebrow">SERVICE DESK</div>
            <h2>数字员工服务台</h2>
            <p>基于企业知识库进行自助排查；涉及权限、生产影响或信息不足时，进入在线记录与人工处理闭环。</p>
            <a-tag class="mt-2" :color="conversationId ? 'green' : 'default'">
              {{ conversationId ? `当前恢复：${conversationTitle} #${conversationId}` : '当前：新会话' }}
            </a-tag>
          </div>
          <a-button :loading="creatingIssue" danger @click="transferToHuman()">创建人工处理记录</a-button>
        </header>

        <div ref="chatBodyRef" class="chat-body">
          <div
            v-for="item in chatMessages"
            :key="item.id"
            :class="['message-row', `message-${item.role}`]"
          >
            <div class="message-avatar">{{ item.role === 'user' ? '我' : item.role === 'system' ? '记' : '云' }}</div>
            <article class="message-bubble">
              <div class="message-meta">
                <span>{{ item.role === 'user' ? '你' : item.role === 'system' ? '系统记录' : '云维 · 运维数字员工' }}</span>
                <small>{{ item.createdAt }}</small>
              </div>
              <a-spin v-if="item.loading" />
              <p class="whitespace-pre-wrap leading-7">{{ item.text }}</p>

              <div v-if="item.role === 'assistant' && !item.loading" class="mt-3 flex flex-wrap gap-2">
                <a-tag v-if="item.status" color="blue">模型状态：{{ item.status }}</a-tag>
                <a-tag v-if="item.intentLabel" color="cyan">意图：{{ item.intentLabel }}</a-tag>
                <a-tag v-if="item.riskLevel" :color="riskColor(item.riskLevel)">风险：{{ riskText(item.riskLevel) }}</a-tag>
                <a-tag v-if="item.confidence !== undefined" color="geekblue">
                  知识置信度：{{ Math.round((item.confidence || 0) * 100) }}%
                </a-tag>
                <a-tag :color="item.reasoningEnabled ? 'gold' : 'default'">
                  {{ item.reasoningEnabled ? '本次启用增强研判' : '本次采用标准响应' }}
                </a-tag>
                <a-tag v-if="item.reasoningEnabled" :color="item.reasoningAvailable ? 'green' : 'default'">
                  {{ item.reasoningAvailable ? '研判链路已安全留存' : '暂无额外研判链路' }}
                </a-tag>
                <a-tag :color="item.needHuman ? 'red' : 'green'">
                  {{ item.needHuman ? '建议人工协同' : '可先自助处理' }}
                </a-tag>
                <a-tag v-if="item.agentMode" color="geekblue">工具协同：{{ item.agentMode }}</a-tag>
                <a-tag v-if="item.issueDraft?.extraction_source" color="processing">
                  记录字段：{{ item.issueDraft.extraction_source }}
                </a-tag>
              </div>

              <div
                v-if="item.role === 'assistant' && !item.loading && (item.missingFields?.length || item.handoffReasons?.length || item.nextActions?.length)"
                class="employee-decision"
              >
                <div>
                  <strong>流程研判</strong>
                  <span>系统已同步完成分类、风险识别、信息完整性检查和后续流转建议。</span>
                </div>
                <div v-if="item.automationSummary?.length" class="decision-list">
                  <span>研判摘要</span>
                  <ul>
                    <li v-for="summary in item.automationSummary" :key="summary">{{ summary }}</li>
                  </ul>
                </div>
                <div v-if="item.missingFields?.length" class="decision-row">
                  <span>待补充信息</span>
                  <a-tag v-for="field in item.missingFields" :key="field" color="orange">{{ field }}</a-tag>
                </div>
                <div v-if="item.issueDraft" class="decision-row">
                  <span>在线记录草案</span>
                  <a-tag color="cyan">{{ item.issueDraft.category || 'general' }}</a-tag>
                  <a-tag color="blue">{{ item.issueDraft.priority || 'medium' }}</a-tag>
                  <a-tag v-if="item.issueDraft.contact_phone" color="green">
                    联系方式：{{ item.issueDraft.contact_phone }}
                  </a-tag>
                  <a-tag v-if="item.issueDraft.impact_scope" color="geekblue">
                    影响：{{ item.issueDraft.impact_scope }}
                  </a-tag>
                </div>
                <div v-if="item.clarificationQuestions?.length" class="decision-actions">
                  <span>补充问题</span>
                  <button
                    v-for="clarification in item.clarificationQuestions"
                    :key="clarification"
                    class="clarify-button"
                    type="button"
                    @click="fillClarification(item.question || '', clarification)"
                  >
                    {{ clarification }}
                  </button>
                </div>
                <div v-if="item.handoffReasons?.length" class="decision-list">
                  <span>人工协同依据</span>
                  <ul>
                    <li v-for="reason in item.handoffReasons" :key="reason">{{ reason }}</li>
                  </ul>
                </div>
                <div v-if="item.nextActions?.length" class="decision-row">
                  <span>建议动作</span>
                  <a-tag v-for="action in item.nextActions" :key="action.key" :color="action.enabled ? 'blue' : 'default'">
                    {{ action.label }}
                  </a-tag>
                </div>
                <div v-if="item.agentTrace?.length" class="agent-trace">
                  <span>工具调用轨迹</span>
                  <ol>
                    <li v-for="(step, index) in item.agentTrace" :key="`${step.phase}-${step.tool}-${index}`">
                      <strong>{{ step.phase }}</strong>
                      <em>{{ step.tool }}</em>
                      <small>{{ step.thought }}</small>
                    </li>
                  </ol>
                </div>
              </div>

              <div v-if="item.references?.length" class="reference-box">
                <div class="reference-title">
                  <span>RAG 引用证据</span>
                  <small v-if="item.rag?.strategy">{{ item.rag.strategy }}</small>
                </div>
                <div v-for="refItem in item.references" :key="refItem.id" class="reference-item">
                  <div class="reference-item-head">
                    <strong>{{ refItem.title }}</strong>
                    <a-tag color="geekblue">{{ Math.round((refItem.score || 0) * 100) }}%</a-tag>
                  </div>
                  <p v-if="refItem.snippet">{{ refItem.snippet }}</p>
                  <div class="reference-meta">
                    <span v-if="refItem.source_type">来源：{{ refItem.source_type }}</span>
                    <span v-if="refItem.version">版本：v{{ refItem.version }}</span>
                    <span v-if="refItem.match_reason">{{ refItem.match_reason }}</span>
                  </div>
                  <div v-if="refItem.matched_terms?.length" class="reference-terms">
                    <a-tag v-for="term in refItem.matched_terms" :key="`${refItem.id}-${term}`" color="cyan">
                      {{ term }}
                    </a-tag>
                  </div>
                </div>
              </div>

              <div v-if="item.role === 'assistant' && !item.loading" class="message-actions">
                <a-button size="small" @click="fillQuestion(item.question || '')">基于该问题继续咨询</a-button>
                <a-button
                  v-if="item.needHuman"
                  :loading="creatingIssue"
                  danger
                  size="small"
                  @click="transferToHuman(item.question || '', item.issueDraft)"
                >
                  使用草案创建在线记录
                </a-button>
              </div>
            </article>
          </div>
        </div>

        <footer class="composer">
          <div class="composer-hint">
            <strong>描述运维诉求</strong>
            <span>建议包含系统名称、账号、错误提示和影响范围；Enter 提交，Shift+Enter 换行。</span>
          </div>
          <label class="input-label" for="ops-chat-input">运维问题描述</label>
          <textarea
            id="ops-chat-input"
            ref="inputRef"
            v-model="question"
            class="composer-input"
            placeholder="例如：我登录 VPN 提示证书过期，影响远程办公，应该怎么处理？"
            rows="4"
            @keydown.enter.exact.prevent="ask()"
          ></textarea>
          <div class="composer-actions">
            <div class="flex flex-wrap gap-2">
              <button
                v-for="item in quickActions.slice(0, 3)"
                :key="item.title"
                class="quick-chip"
                type="button"
                @click="fillQuestion(item.query)"
              >
                {{ item.title }}
              </button>
            </div>
            <div class="flex gap-2">
              <button class="secondary-send" :disabled="creatingIssue" type="button" @click="transferToHuman()">
                {{ creatingIssue ? '创建中...' : '创建在线记录' }}
              </button>
              <button class="primary-send" :disabled="loading" type="button" @click="ask()">
                {{ loading ? '分析中...' : '提交咨询' }}
              </button>
            </div>
          </div>
        </footer>
      </main>
    </section>
  </div>
</template>

<style scoped>
.ops-chat-page {
  --accent: #0f766e;
  --accent-strong: #0f3f3a;
  --ink: #0f172a;
  --muted: #64748b;
  background:
    linear-gradient(120deg, rgb(20 184 166 / 8%) 0 1px, transparent 1px 68px),
    linear-gradient(150deg, #f8fafc 0%, #edf8f4 45%, #fff7ed 100%);
  min-height: calc(100vh - 96px);
  padding: 20px;
  position: relative;
}

.ops-chat-page :deep(.ant-tag) {
  align-items: center;
  display: inline-flex;
  height: auto;
  line-height: 1.35;
  margin-inline-end: 0;
  max-width: 100%;
  min-height: 22px;
  min-width: 0;
  overflow-wrap: anywhere;
  white-space: normal;
  word-break: break-word;
}

.ops-chat-page::before {
  background:
    linear-gradient(90deg, rgb(15 23 42 / 6%) 1px, transparent 1px),
    linear-gradient(rgb(15 23 42 / 5%) 1px, transparent 1px);
  background-size: 42px 42px;
  content: '';
  inset: 0;
  mask-image: linear-gradient(to bottom, #000, transparent 70%);
  pointer-events: none;
  position: absolute;
}

.chat-shell {
  align-items: start;
  display: grid;
  gap: 18px;
  grid-template-columns: 360px minmax(0, 1fr);
  margin: 0 auto;
  max-width: 1480px;
  position: relative;
}

.chat-sidebar,
.chat-main {
  border: 1px solid rgb(15 118 110 / 14%);
  border-radius: 8px;
  box-shadow: 0 24px 70px rgb(15 23 42 / 10%);
}

.chat-sidebar {
  backdrop-filter: blur(18px);
  background: rgb(255 255 255 / 78%);
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: calc(100vh - 136px);
  min-height: 640px;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 16px;
  position: sticky;
  top: 20px;
}

.chat-sidebar > * {
  flex: 0 0 auto;
}

.brand-card,
.status-card,
.session-card,
.suggest-card {
  background: rgb(255 255 255 / 88%);
  border: 1px solid rgb(15 23 42 / 7%);
  border-radius: 8px;
  box-shadow: 0 14px 34px rgb(15 23 42 / 6%);
  padding: 16px;
}

.brand-card {
  align-items: center;
  background:
    linear-gradient(120deg, rgb(255 255 255 / 8%) 0 1px, transparent 1px 42px),
    linear-gradient(135deg, #0f172a, #115e59);
  color: #fff;
  display: flex;
  gap: 14px;
  overflow: hidden;
  position: relative;
}

.brand-card::after {
  background: linear-gradient(90deg, #38bdf8, #14b8a6, #f59e0b);
  content: '';
  height: 3px;
  inset: auto 0 0;
  position: absolute;
}

.avatar,
.message-avatar {
  align-items: center;
  background: linear-gradient(135deg, #f59e0b, #14b8a6);
  border-radius: 8px;
  color: #fff;
  display: flex;
  flex: 0 0 auto;
  font-weight: 800;
  height: 46px;
  justify-content: center;
  width: 46px;
}

.brand-card h1,
.chat-header h2,
.session-card h2,
.suggest-card h2 {
  font-weight: 800;
  margin: 0;
}

.brand-card p,
.chat-header p,
.session-card p,
.suggest-card p,
.composer-hint span {
  color: rgb(255 255 255 / 72%);
  margin: 4px 0 0;
}

.session-card p,
.suggest-card p,
.composer-hint span {
  color: var(--muted);
  font-size: 12px;
}

.eyebrow {
  color: #99f6e4;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0;
}

.status-row {
  align-items: center;
  background: #f8fafc;
  border: 1px solid rgb(15 23 42 / 6%);
  border-radius: 8px;
  display: flex;
  justify-content: space-between;
  margin-top: 8px;
  padding: 8px 10px;
}

.metrics-grid {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(4, 1fr);
}

.metrics-grid div {
  background: linear-gradient(180deg, #fff, #f8fafc);
  border: 1px solid rgb(15 23 42 / 7%);
  border-radius: 8px;
  box-shadow: 0 12px 28px rgb(15 23 42 / 5%);
  padding: 12px;
}

.metrics-grid strong,
.metrics-grid span {
  display: block;
}

.metrics-grid strong {
  color: var(--accent-strong);
  font-size: 22px;
}

.metrics-grid span {
  color: var(--muted);
  font-size: 12px;
}

.suggest-button,
.session-button,
.quick-button {
  background: linear-gradient(180deg, #fff, #f8fafc);
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  cursor: pointer;
  display: block;
  margin-top: 8px;
  padding: 12px;
  text-align: left;
  transition: all 0.16s ease;
  width: 100%;
}

.suggest-button:hover,
.session-button:hover,
.quick-button:hover {
  background: #f0fdfa;
  border-color: #2dd4bf;
  box-shadow: 0 12px 24px rgb(15 23 42 / 8%);
  transform: translateY(-1px);
}

.suggest-button span,
.session-button span,
.quick-button strong,
.quick-button span {
  display: block;
}

.suggest-button small,
.session-button small,
.quick-button span {
  color: var(--muted);
  font-size: 12px;
  margin-top: 4px;
}

.session-button.active {
  background:
    linear-gradient(90deg, rgb(15 118 110 / 14%) 0 4px, transparent 4px),
    linear-gradient(135deg, #ecfeff, #fff7ed);
  border-color: #14b8a6;
  box-shadow: 0 12px 28px rgb(15 23 42 / 8%);
}

.session-button em {
  color: #94a3b8;
  display: block;
  font-size: 11px;
  font-style: normal;
  margin-top: 6px;
}

.empty-session {
  background: #f8fafc;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  color: var(--muted);
  font-size: 12px;
  padding: 12px;
}

.chat-main {
  backdrop-filter: blur(18px);
  background:
    linear-gradient(180deg, rgb(255 255 255 / 90%), rgb(255 255 255 / 76%));
  display: flex;
  flex-direction: column;
  height: calc(100vh - 136px);
  min-height: 640px;
  min-width: 0;
  overflow: hidden;
}

.chat-header {
  align-items: center;
  background:
    linear-gradient(120deg, rgb(15 118 110 / 7%) 0 1px, transparent 1px 48px),
    linear-gradient(180deg, rgb(255 255 255 / 92%), rgb(248 250 252 / 70%));
  border-bottom: 1px solid rgb(15 23 42 / 8%);
  display: flex;
  justify-content: space-between;
  padding: 20px 24px;
}

.chat-header .eyebrow {
  color: var(--accent);
}

.chat-header p {
  color: var(--muted);
}

.chat-body {
  flex: 1 1 auto;
  background:
    linear-gradient(90deg, rgb(15 23 42 / 4%) 1px, transparent 1px),
    linear-gradient(rgb(15 23 42 / 3%) 1px, transparent 1px);
  background-size: 30px 30px;
  min-height: 0;
  overflow-y: auto;
  padding: 24px;
}

.message-row {
  display: flex;
  gap: 12px;
  margin-bottom: 18px;
}

.message-user {
  flex-direction: row-reverse;
}

.message-user .message-bubble {
  background: linear-gradient(135deg, #0f766e, #134e4a);
  color: #fff;
}

.message-system .message-bubble {
  background: #fff7ed;
  border-color: #fed7aa;
}

.message-bubble {
  background: rgb(255 255 255 / 90%);
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  box-shadow: 0 14px 34px rgb(15 23 42 / 7%);
  max-width: min(780px, 86%);
  min-width: 0;
  overflow-wrap: anywhere;
  padding: 14px 16px;
  word-break: break-word;
}

.message-meta {
  align-items: center;
  display: flex;
  gap: 10px;
  margin-bottom: 8px;
}

.message-meta span {
  font-weight: 700;
}

.message-meta small {
  color: var(--muted);
}

.message-user .message-meta small {
  color: rgb(255 255 255 / 65%);
}

.reference-box {
  background: rgb(255 255 255 / 70%);
  border: 1px solid rgb(15 23 42 / 7%);
  border-radius: 8px;
  display: grid;
  gap: 8px;
  margin-top: 12px;
  padding: 10px;
}

.employee-decision {
  background: linear-gradient(135deg, rgb(240 253 250 / 86%), rgb(255 251 235 / 76%));
  border: 1px solid rgb(20 184 166 / 20%);
  border-radius: 8px;
  color: #334155;
  margin-top: 12px;
  padding: 12px;
}

.employee-decision strong,
.employee-decision span {
  display: block;
}

.employee-decision > div:first-child span {
  color: var(--muted);
  font-size: 12px;
  margin-top: 2px;
}

.decision-row,
.decision-list,
.agent-trace,
.decision-actions {
  margin-top: 10px;
}

.decision-row > span,
.decision-list > span,
.agent-trace > span,
.decision-actions > span {
  color: var(--accent-strong);
  font-size: 12px;
  font-weight: 800;
  margin-bottom: 6px;
}

.decision-list ul {
  margin: 4px 0 0 18px;
  padding: 0;
}

.agent-trace ol {
  counter-reset: react-step;
  display: grid;
  gap: 6px;
  margin: 6px 0 0;
  padding: 0;
}

.agent-trace li {
  align-items: start;
  background: rgb(255 255 255 / 74%);
  border: 1px solid #ccfbf1;
  border-radius: 8px;
  display: grid;
  gap: 3px;
  grid-template-columns: minmax(56px, 0.6fr) minmax(96px, 1fr) minmax(0, 2.4fr);
  list-style: none;
  min-width: 0;
  padding: 8px 10px;
}

.agent-trace strong,
.agent-trace em,
.agent-trace small {
  display: block;
  min-width: 0;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.agent-trace strong {
  color: #0f766e;
}

.agent-trace em {
  color: #a16207;
  font-style: normal;
  font-weight: 700;
}

.agent-trace small {
  color: var(--muted);
}

.clarify-button {
  background: #fff;
  border: 1px solid #bae6fd;
  border-radius: 8px;
  color: #0369a1;
  cursor: pointer;
  font-size: 12px;
  margin: 4px 6px 0 0;
  padding: 6px 10px;
}

.clarify-button:hover {
  background: #ecfeff;
  border-color: #22d3ee;
}

.reference-title {
  align-items: center;
  color: var(--muted);
  display: flex;
  font-size: 12px;
  justify-content: space-between;
  margin-bottom: 8px;
}

.reference-title span {
  color: #0f766e;
  font-weight: 800;
}

.reference-title small {
  color: var(--muted);
}

.reference-item {
  background: rgb(248 250 252 / 78%);
  border: 1px solid #dbeafe;
  border-radius: 8px;
  padding: 9px 10px;
}

.reference-item-head,
.reference-meta,
.reference-terms {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.reference-item-head {
  justify-content: space-between;
}

.reference-item-head strong {
  color: #0f172a;
  min-width: 0;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.reference-item p {
  color: #475569;
  line-height: 1.55;
  margin: 6px 0;
}

.reference-meta {
  color: var(--muted);
  font-size: 12px;
}

.reference-terms {
  margin-top: 6px;
}

.message-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.composer {
  background:
    linear-gradient(180deg, rgb(255 255 255 / 94%), #fff);
  border-top: 1px solid rgb(15 23 42 / 8%);
  box-shadow: 0 -18px 48px rgb(15 23 42 / 10%);
  flex: 0 0 auto;
  padding: 16px 20px 18px;
  position: relative;
  z-index: 30;
}

.composer,
.composer * {
  pointer-events: auto;
}

.composer-hint {
  align-items: baseline;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 10px;
}

.composer-hint strong {
  color: var(--accent-strong);
}

.input-label {
  color: var(--accent-strong);
  display: block;
  font-size: 13px;
  font-weight: 800;
  margin-bottom: 8px;
}

.composer-input {
  background: #fff;
  border: 2px solid #0f766e;
  border-radius: 8px;
  box-shadow:
    0 0 0 5px rgb(20 184 166 / 13%),
    inset 0 1px 0 rgb(15 23 42 / 5%);
  color: var(--ink);
  display: block;
  font-size: 16px;
  line-height: 1.7;
  min-height: 118px;
  outline: none;
  padding: 14px 16px;
  resize: vertical;
  width: 100%;
}

.composer-input:focus {
  border-color: #f59e0b;
  box-shadow:
    0 0 0 6px rgb(245 158 11 / 18%),
    0 16px 34px rgb(15 23 42 / 12%);
}

.composer-actions {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  justify-content: space-between;
  margin-top: 12px;
}

.quick-chip,
.primary-send,
.secondary-send {
  border: 0;
  border-radius: 8px;
  cursor: pointer;
  font-weight: 700;
}

.quick-chip {
  background: #ecfeff;
  color: #0f766e;
  padding: 7px 12px;
}

.primary-send,
.secondary-send {
  min-height: 40px;
  padding: 0 18px;
}

.primary-send {
  background: linear-gradient(135deg, #0f766e, #14b8a6);
  color: #fff;
}

.secondary-send {
  background: #f1f5f9;
  color: #334155;
}

.primary-send:disabled,
.secondary-send:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}

/* Service-desk cockpit pass. This page should feel like an AI service console,
   not a generic admin form. */
.ops-chat-page {
  --accent: #14b8a6;
  --accent-strong: #5eead4;
  --ink: #e5f4f4;
  --muted: #9fb3c8;
  background:
    linear-gradient(120deg, rgb(34 211 238 / 10%) 0 1px, transparent 1px 72px),
    linear-gradient(155deg, #050b12 0%, #081a24 48%, #17110b 100%);
  color: var(--ink);
}

.ops-chat-page::before {
  background:
    linear-gradient(90deg, rgb(125 211 252 / 7%) 1px, transparent 1px),
    linear-gradient(rgb(125 211 252 / 5%) 1px, transparent 1px);
}

.chat-sidebar,
.chat-main {
  border-color: rgb(148 163 184 / 18%);
  box-shadow:
    0 26px 82px rgb(0 0 0 / 30%),
    inset 0 1px 0 rgb(255 255 255 / 7%);
}

.chat-sidebar {
  background:
    linear-gradient(180deg, rgb(15 23 42 / 82%), rgb(8 20 31 / 78%));
}

.status-card,
.session-card,
.suggest-card,
.metrics-grid div {
  background: rgb(15 23 42 / 72%);
  border-color: rgb(148 163 184 / 16%);
  box-shadow: 0 14px 34px rgb(0 0 0 / 18%);
}

.session-card h2,
.suggest-card h2,
.chat-header h2,
.message-meta span,
.reference-item-head strong {
  color: #f8fafc;
}

.status-row,
.suggest-button,
.session-button,
.quick-button,
.empty-session {
  background: rgb(8 20 31 / 74%);
  border-color: rgb(148 163 184 / 16%);
  color: var(--ink);
}

.status-row span,
.suggest-button small,
.session-button small,
.quick-button span,
.session-button em,
.empty-session,
.chat-header p,
.session-card p,
.suggest-card p,
.composer-hint span,
.message-meta small,
.reference-title,
.reference-title small,
.reference-meta,
.agent-trace small {
  color: var(--muted);
}

.suggest-button:hover,
.session-button:hover,
.quick-button:hover,
.session-button.active {
  background: rgb(8 47 73 / 82%);
  border-color: rgb(94 234 212 / 54%);
  box-shadow: 0 16px 34px rgb(0 0 0 / 24%);
}

.metrics-grid strong,
.composer-hint strong,
.input-label,
.decision-row > span,
.decision-list > span,
.agent-trace > span,
.decision-actions > span,
.reference-title span {
  color: var(--accent-strong);
}

.chat-main {
  background:
    linear-gradient(180deg, rgb(15 23 42 / 88%), rgb(8 20 31 / 80%));
}

.chat-header {
  background:
    linear-gradient(120deg, rgb(45 212 191 / 12%) 0 1px, transparent 1px 52px),
    linear-gradient(135deg, rgb(15 23 42 / 94%), rgb(8 47 73 / 72));
  border-bottom-color: rgb(148 163 184 / 16%);
}

.chat-header .eyebrow {
  color: #67e8f9;
}

.chat-body {
  background:
    linear-gradient(90deg, rgb(125 211 252 / 6%) 1px, transparent 1px),
    linear-gradient(rgb(125 211 252 / 4%) 1px, transparent 1px);
  background-size: 32px 32px;
  position: relative;
}

.chat-body::before {
  background: linear-gradient(90deg, transparent, rgb(20 184 166 / 16%), transparent);
  content: '';
  height: 1px;
  inset: 28px 24px auto;
  pointer-events: none;
  position: absolute;
}

.message-bubble,
.reference-box,
.employee-decision,
.reference-item,
.agent-trace li {
  background: rgb(15 23 42 / 74%);
  border-color: rgb(148 163 184 / 16%);
  color: var(--ink);
}

.message-user .message-bubble {
  background: linear-gradient(135deg, #0f766e, #115e59);
  border-color: rgb(94 234 212 / 34%);
}

.message-system .message-bubble {
  background: rgb(69 26 3 / 62%);
  border-color: rgb(245 158 11 / 24%);
}

.employee-decision > div:first-child span,
.reference-item p {
  color: var(--muted);
}

.agent-trace strong,
.reference-title span {
  color: #5eead4;
}

.agent-trace em {
  color: #fde68a;
}

.clarify-button,
.quick-chip {
  background: rgb(8 47 73 / 72%);
  border-color: rgb(103 232 249 / 24%);
  color: #a5f3fc;
}

.composer {
  background:
    linear-gradient(180deg, rgb(15 23 42 / 92%), rgb(8 20 31 / 96%));
  border-top-color: rgb(148 163 184 / 16%);
}

.composer-input {
  background: rgb(2 6 23 / 70%);
  border-color: #14b8a6;
  color: #f8fafc;
}

.composer-input::placeholder {
  color: #64748b;
}

.secondary-send {
  background: rgb(15 23 42 / 92%);
  border: 1px solid rgb(148 163 184 / 18%);
  color: #cbd5e1;
}

.ops-chat-page :deep(.ant-tag-default) {
  background: rgb(15 23 42 / 72%);
  border-color: rgb(148 163 184 / 22%);
  color: #e5f4f4;
}

.ops-chat-page :deep(.ant-tag-green) {
  background: rgb(20 83 45 / 42%);
  border-color: rgb(74 222 128 / 28%);
  color: #86efac;
}

/* The service desk has a custom cockpit look, but it still needs to respect the
   user's light theme. These overrides come after the cockpit pass so Ant tags
   and message metadata do not inherit dark-page colors on light backgrounds. */
:global(html:not(.dark)) .ops-chat-page {
  --accent: #0f766e;
  --accent-strong: #0f3f3a;
  --ink: #0f172a;
  --muted: #64748b;
  background:
    linear-gradient(120deg, rgb(20 184 166 / 8%) 0 1px, transparent 1px 68px),
    linear-gradient(150deg, #f8fafc 0%, #edf8f4 45%, #fff7ed 100%);
  color: var(--ink);
}

:global(html:not(.dark)) .ops-chat-page::before {
  background:
    linear-gradient(90deg, rgb(15 23 42 / 6%) 1px, transparent 1px),
    linear-gradient(rgb(15 23 42 / 5%) 1px, transparent 1px);
}

:global(html:not(.dark)) .chat-sidebar {
  background: rgb(255 255 255 / 78%);
}

:global(html:not(.dark)) .status-card,
:global(html:not(.dark)) .session-card,
:global(html:not(.dark)) .suggest-card,
:global(html:not(.dark)) .metrics-grid div {
  background: rgb(255 255 255 / 88%);
  border-color: rgb(15 23 42 / 7%);
  box-shadow: 0 14px 34px rgb(15 23 42 / 6%);
}

:global(html:not(.dark)) .session-card h2,
:global(html:not(.dark)) .suggest-card h2,
:global(html:not(.dark)) .chat-header h2,
:global(html:not(.dark)) .message-meta span,
:global(html:not(.dark)) .reference-item-head strong {
  color: #0f172a;
}

:global(html:not(.dark)) .status-row,
:global(html:not(.dark)) .suggest-button,
:global(html:not(.dark)) .session-button,
:global(html:not(.dark)) .quick-button,
:global(html:not(.dark)) .empty-session {
  background: linear-gradient(180deg, #fff, #f8fafc);
  border-color: #e2e8f0;
  color: var(--ink);
}

:global(html:not(.dark)) .status-row span,
:global(html:not(.dark)) .suggest-button small,
:global(html:not(.dark)) .session-button small,
:global(html:not(.dark)) .quick-button span,
:global(html:not(.dark)) .session-button em,
:global(html:not(.dark)) .empty-session,
:global(html:not(.dark)) .chat-header p,
:global(html:not(.dark)) .session-card p,
:global(html:not(.dark)) .suggest-card p,
:global(html:not(.dark)) .composer-hint span,
:global(html:not(.dark)) .message-meta small,
:global(html:not(.dark)) .reference-title,
:global(html:not(.dark)) .reference-title small,
:global(html:not(.dark)) .reference-meta,
:global(html:not(.dark)) .agent-trace small {
  color: var(--muted);
}

:global(html:not(.dark)) .metrics-grid strong,
:global(html:not(.dark)) .composer-hint strong,
:global(html:not(.dark)) .input-label,
:global(html:not(.dark)) .decision-row > span,
:global(html:not(.dark)) .decision-list > span,
:global(html:not(.dark)) .agent-trace > span,
:global(html:not(.dark)) .decision-actions > span,
:global(html:not(.dark)) .reference-title span {
  color: var(--accent-strong);
}

:global(html:not(.dark)) .chat-main {
  background:
    linear-gradient(180deg, rgb(255 255 255 / 90%), rgb(255 255 255 / 76%));
}

:global(html:not(.dark)) .chat-header {
  background:
    linear-gradient(120deg, rgb(15 118 110 / 7%) 0 1px, transparent 1px 48px),
    linear-gradient(180deg, rgb(255 255 255 / 92%), rgb(248 250 252 / 70%));
  border-bottom-color: rgb(15 23 42 / 8%);
}

:global(html:not(.dark)) .chat-header .eyebrow {
  color: var(--accent);
}

:global(html:not(.dark)) .chat-body {
  background:
    linear-gradient(90deg, rgb(15 23 42 / 4%) 1px, transparent 1px),
    linear-gradient(rgb(15 23 42 / 3%) 1px, transparent 1px);
  background-size: 30px 30px;
}

:global(html:not(.dark)) .message-bubble,
:global(html:not(.dark)) .reference-box,
:global(html:not(.dark)) .employee-decision,
:global(html:not(.dark)) .reference-item,
:global(html:not(.dark)) .agent-trace li {
  background: rgb(255 255 255 / 90%);
  border-color: #e2e8f0;
  color: var(--ink);
}

:global(html:not(.dark)) .message-system .message-bubble {
  background: #fff7ed;
  border-color: #fed7aa;
}

:global(html:not(.dark)) .employee-decision {
  background: linear-gradient(135deg, rgb(240 253 250 / 86%), rgb(255 251 235 / 76%));
  border-color: rgb(20 184 166 / 20%);
}

:global(html:not(.dark)) .employee-decision > div:first-child span,
:global(html:not(.dark)) .reference-item p {
  color: var(--muted);
}

:global(html:not(.dark)) .agent-trace strong,
:global(html:not(.dark)) .reference-title span {
  color: #0f766e;
}

:global(html:not(.dark)) .agent-trace em {
  color: #a16207;
}

:global(html:not(.dark)) .clarify-button,
:global(html:not(.dark)) .quick-chip {
  background: #ecfeff;
  border-color: #bae6fd;
  color: #0369a1;
}

:global(html:not(.dark)) .composer {
  background:
    linear-gradient(180deg, rgb(255 255 255 / 94%), #fff);
  border-top-color: rgb(15 23 42 / 8%);
}

:global(html:not(.dark)) .composer-input {
  background: #fff;
  border-color: #0f766e;
  color: #0f172a;
}

:global(html:not(.dark)) .secondary-send {
  background: #f1f5f9;
  border-color: transparent;
  color: #334155;
}

:global(html:not(.dark)) .ops-chat-page :deep(.ant-tag-default) {
  background: #f8fafc;
  border-color: #cbd5e1;
  color: #334155;
}

:global(html:not(.dark)) .ops-chat-page :deep(.ant-tag-green) {
  background: #f0fdf4;
  border-color: #bbf7d0;
  color: #166534;
}

@media (max-width: 1180px) {
  .chat-shell {
    grid-template-columns: 1fr;
  }

  .chat-sidebar {
    height: auto;
    max-height: none;
    min-height: 0;
    overflow: visible;
    position: static;
  }

  .chat-main {
    height: auto;
    min-height: 680px;
  }
}

@media (max-width: 640px) {
  .ops-chat-page {
    padding: 10px;
  }

  .chat-header,
  .composer-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .message-bubble {
    max-width: 100%;
  }

  .metrics-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
