<script lang="ts" setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue';

import { message } from 'ant-design-vue';

import { askQuestion, createIssue, draftIssue, getLlmStatus, getStats, suggestQuestions } from '#/api/ops';

type ChatRole = 'assistant' | 'system' | 'user';

interface ChatMessage {
  answer?: string;
  createdAt: string;
  id: string;
  loading?: boolean;
  needHuman?: boolean;
  question?: string;
  reasoningAvailable?: boolean;
  reasoningEnabled?: boolean;
  references?: any[];
  role: ChatRole;
  status?: string;
  text: string;
}

const question = ref('');
const currentQuestion = ref('');
const needHuman = ref(false);
const modelStatus = ref('');
const loading = ref(false);
const creatingIssue = ref(false);
const stats = ref<any>({});
const llmStatus = ref<any>({});
const suggestions = ref<any[]>([]);
const suggestLoading = ref(false);
const enableThinking = ref(false);
const chatBodyRef = ref<HTMLElement | null>(null);
const inputRef = ref<HTMLTextAreaElement | null>(null);
let suggestTimer: ReturnType<typeof setTimeout> | undefined;

const quickActions = [
  { query: '账号被冻结了，怎么恢复使用？', title: '账号冻结', desc: '自助解冻、热线和转人工边界' },
  { query: 'VPN 无法连接，应该怎么排查？', title: 'VPN 故障', desc: '网络、证书、客户端版本排查' },
  { query: '我需要申请业务系统权限，需要准备哪些信息？', title: '权限申请', desc: '字段、审批、审计要求' },
  { query: '数据库连接失败，怎么判断影响范围？', title: '数据库连接', desc: '连接串、日志和影响范围' },
];

const chatMessages = ref<ChatMessage[]>([
  {
    createdAt: new Date().toLocaleTimeString(),
    id: 'welcome',
    role: 'assistant',
    text: '你好，我是云维，企业运维数字员工。请直接在下方输入你的问题，例如账号、VPN、邮箱、权限、系统慢或数据库连接失败。我会先基于私有知识库回答，必要时帮你转人工生成在线记录。',
  },
]);

const llmModeText = computed(() => {
  if (!llmStatus.value.employee_name) return '检测中';
  if (llmStatus.value.ready) return `本地 ${llmStatus.value.vllm_model_name} 已接入`;
  return 'LLM 未就绪，请先启动 vLLM';
});

const visibleSuggestions = computed(() => suggestions.value.slice(0, 6));

const thinkingModeText = computed(() => {
  if (enableThinking.value) return '深度推理';
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

async function ask(text = question.value) {
  const rawQuestion = text.trim();
  if (!rawQuestion) {
    await loadSuggestions('');
    message.info('请在底部输入框里描述你的问题，或点击左侧推荐查询');
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
    text: '云维正在查询私有知识库并组织处理步骤...',
  });
  await scrollToBottom();

  try {
    const result = await askQuestion(rawQuestion, enableThinking.value);
    const assistantMessage = chatMessages.value.find((item) => item.id === assistantMessageId);
    if (assistantMessage) {
      assistantMessage.answer = result.answer;
      assistantMessage.loading = false;
      assistantMessage.needHuman = result.need_human;
      assistantMessage.question = rawQuestion;
      assistantMessage.reasoningAvailable = !!result.reasoning_available;
      assistantMessage.reasoningEnabled = !!result.reasoning_enabled;
      assistantMessage.references = result.references || [];
      assistantMessage.status = result.model_status;
      assistantMessage.text = result.answer;
    }
    needHuman.value = result.need_human;
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
    await loadStats();
  } catch (error: any) {
    const assistantMessage = chatMessages.value.find((item) => item.id === assistantMessageId);
    if (assistantMessage) {
      assistantMessage.loading = false;
      assistantMessage.needHuman = true;
      assistantMessage.question = rawQuestion;
      assistantMessage.status = 'unavailable';
      assistantMessage.text = '数字员工暂时不可用。请确认 vLLM 已启动，或先创建在线记录让运维人员处理。';
    }
    needHuman.value = true;
    message.error(error?.message || '数字员工请求失败');
  } finally {
    loading.value = false;
    await scrollToBottom();
  }
}

async function transferToHuman(text = currentQuestion.value || question.value) {
  const issueText = text.trim();
  if (!issueText) {
    message.warning('请先输入问题描述，再创建在线记录');
    return;
  }
  creatingIssue.value = true;
  try {
    const draft = await draftIssue(issueText);
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
      text: `已创建在线记录，并自动补齐草稿字段：分类 ${draft.category}、优先级 ${draft.priority}、影响范围 ${draft.impact_scope || '未识别'}。${missingText}\n你可以到“在线记录”页面查看处理状态，运维人员处理后会回访并沉淀知识库。`,
    });
    message.success('已创建在线记录，运维人员可在“在线记录”中处理和回访');
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
  await Promise.all([loadStats(), loadLlmStatus(), loadSuggestions('')]);
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
            <h1>和数字员工对话</h1>
            <p>在右侧底部输入框直接提问，这是主要沟通入口。</p>
          </div>
        </div>

        <div class="status-card">
          <div class="status-row">
            <span>模型状态</span>
            <a-tag :color="llmStatus.ready ? 'green' : 'red'">{{ llmModeText }}</a-tag>
          </div>
          <div class="status-row">
            <span>回答模式</span>
            <a-tag :color="enableThinking ? 'gold' : 'blue'">{{ thinkingModeText }}</a-tag>
          </div>
          <div class="mt-3 flex items-center justify-between rounded-2xl bg-white/70 px-3 py-2">
            <span class="text-sm text-slate-600">深度推理 / think</span>
            <a-switch v-model:checked="enableThinking" />
          </div>
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
              <h2>可点选的常见问题</h2>
              <p>也可以无视这里，直接在右侧输入。</p>
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
            <div class="eyebrow">CHAT WITH YUNWEI</div>
            <h2>数字员工服务台</h2>
            <p>像 ChatGPT 一样对话：描述问题，发送，查看处理步骤；解决不了就转人工。</p>
          </div>
          <a-button :loading="creatingIssue" danger @click="transferToHuman()">转人工</a-button>
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
                <a-tag :color="item.reasoningEnabled ? 'gold' : 'default'">
                  {{ item.reasoningEnabled ? '本次启用 reasoning' : '快速 no_think' }}
                </a-tag>
                <a-tag v-if="item.reasoningEnabled" :color="item.reasoningAvailable ? 'green' : 'default'">
                  {{ item.reasoningAvailable ? '已收到 reasoning_content（不展示）' : '未检测到 reasoning_content' }}
                </a-tag>
                <a-tag :color="item.needHuman ? 'red' : 'green'">
                  {{ item.needHuman ? '建议转人工' : '可先自助处理' }}
                </a-tag>
              </div>

              <div v-if="item.references?.length" class="reference-box">
                <div class="reference-title">引用知识来源</div>
                <a-tag v-for="refItem in item.references" :key="refItem.id" color="geekblue">
                  {{ refItem.title }} · {{ Math.round((refItem.score || 0) * 100) }}%
                </a-tag>
              </div>

              <div v-if="item.role === 'assistant' && !item.loading" class="message-actions">
                <a-button size="small" @click="fillQuestion(item.question || '')">追问这个问题</a-button>
                <a-button
                  v-if="item.needHuman"
                  :loading="creatingIssue"
                  danger
                  size="small"
                  @click="transferToHuman(item.question || '')"
                >
                  按此问题转人工
                </a-button>
              </div>
            </article>
          </div>
        </div>

        <footer class="composer">
          <div class="composer-hint">
            <strong>在这里和数字员工沟通</strong>
            <span>输入系统名称、账号、错误提示、影响范围；Enter 发送，Shift+Enter 换行。</span>
          </div>
          <label class="input-label" for="ops-chat-input">问题输入框</label>
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
                {{ loading ? '发送中...' : '发送给云维' }}
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
    radial-gradient(circle at 4% 4%, rgb(20 184 166 / 18%), transparent 32%),
    radial-gradient(circle at 96% 10%, rgb(245 158 11 / 16%), transparent 28%),
    linear-gradient(180deg, #f8fafc, #eef6f3);
  min-height: calc(100vh - 96px);
  padding: 20px;
}

.chat-shell {
  display: grid;
  gap: 18px;
  grid-template-columns: 360px minmax(0, 1fr);
  margin: 0 auto;
  max-width: 1480px;
}

.chat-sidebar,
.chat-main {
  border: 1px solid rgb(15 118 110 / 14%);
  border-radius: 30px;
  box-shadow: 0 24px 70px rgb(15 23 42 / 10%);
}

.chat-sidebar {
  background: rgb(255 255 255 / 72%);
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
}

.brand-card,
.status-card,
.suggest-card {
  background: #fff;
  border-radius: 24px;
  padding: 16px;
}

.brand-card {
  align-items: center;
  background:
    radial-gradient(circle at 88% 12%, rgb(45 212 191 / 35%), transparent 34%),
    linear-gradient(135deg, #0f172a, #115e59);
  color: #fff;
  display: flex;
  gap: 14px;
}

.avatar,
.message-avatar {
  align-items: center;
  background: linear-gradient(135deg, #f59e0b, #14b8a6);
  border-radius: 18px;
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
.suggest-card h2 {
  font-weight: 800;
  margin: 0;
}

.brand-card p,
.chat-header p,
.suggest-card p,
.composer-hint span {
  color: rgb(255 255 255 / 72%);
  margin: 4px 0 0;
}

.suggest-card p,
.composer-hint span {
  color: var(--muted);
  font-size: 12px;
}

.eyebrow {
  color: #99f6e4;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.16em;
}

.status-row {
  align-items: center;
  display: flex;
  justify-content: space-between;
  margin-top: 8px;
}

.metrics-grid {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(4, 1fr);
}

.metrics-grid div {
  background: #fff;
  border-radius: 18px;
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
.quick-button {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  cursor: pointer;
  display: block;
  margin-top: 8px;
  padding: 12px;
  text-align: left;
  transition: all 0.16s ease;
  width: 100%;
}

.suggest-button:hover,
.quick-button:hover {
  background: #ecfeff;
  border-color: #2dd4bf;
  transform: translateY(-1px);
}

.suggest-button span,
.quick-button strong,
.quick-button span {
  display: block;
}

.suggest-button small,
.quick-button span {
  color: var(--muted);
  font-size: 12px;
  margin-top: 4px;
}

.chat-main {
  background: rgb(255 255 255 / 84%);
  display: flex;
  flex-direction: column;
  height: calc(100vh - 136px);
  min-height: 720px;
  min-width: 0;
  overflow: hidden;
}

.chat-header {
  align-items: center;
  border-bottom: 1px solid #e2e8f0;
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
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 20px;
  max-width: min(780px, 86%);
  padding: 14px 16px;
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
  border-radius: 16px;
  margin-top: 12px;
  padding: 10px;
}

.reference-title {
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 8px;
}

.message-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.composer {
  background: #fff;
  border-top: 1px solid #e2e8f0;
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
  border: 3px solid #0f766e;
  border-radius: 20px;
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
  border-radius: 14px;
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

@media (max-width: 1180px) {
  .chat-shell {
    grid-template-columns: 1fr;
  }

  .chat-main {
    height: auto;
    min-height: 780px;
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
