<script lang="ts" setup>
import { computed, onMounted, ref, watch } from 'vue';

import { message } from 'ant-design-vue';

import { askQuestion, createIssue, getLlmStatus, getStats, suggestQuestions } from '#/api/ops';

const question = ref('');
const answer = ref('');
const references = ref<any[]>([]);
const needHuman = ref(false);
const modelStatus = ref('');
const loading = ref(false);
const creatingIssue = ref(false);
const stats = ref<any>({});
const llmStatus = ref<any>({});
const suggestions = ref<any[]>([]);
const suggestLoading = ref(false);
const showSuggestions = ref(true);
let suggestTimer: ReturnType<typeof setTimeout> | undefined;

const llmModeText = computed(() => {
  if (!llmStatus.value.employee_name) return '检测中';
  if (llmStatus.value.ready) return `已接入本地 ${llmStatus.value.vllm_model_name}`;
  return 'LLM 未就绪，请先启动 vLLM';
});

const guideSteps = [
  { title: '1. 先描述问题', text: '输入系统、账号、错误提示或现象；也可以直接点击推荐问题。' },
  { title: '2. 查看知识来源', text: '数字员工基于私有知识库回答，并展示命中的 FAQ / Runbook。' },
  { title: '3. 无法解决就转人工', text: '低置信度、高风险或仍未解决的问题，一键创建在线记录。' },
];

const quickActions = [
  { query: '账号冻结怎么处理？', title: '账号冻结', desc: '自助解冻、热线与转人工边界' },
  { query: 'VPN 无法连接怎么排查？', title: 'VPN 故障', desc: '网络、证书、客户端版本排查' },
  { query: '申请系统权限需要哪些信息？', title: '权限申请', desc: '申请字段、审批与审计要求' },
  { query: '数据库连接失败怎么处理？', title: '数据库连接', desc: '影响范围、连接串、日志定位' },
];

const hasQuestion = computed(() => question.value.trim().length > 0);
const visibleSuggestions = computed(() => suggestions.value.slice(0, 8));

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

function chooseSuggestion(item: any) {
  question.value = item.query || item.title;
  showSuggestions.value = false;
}

async function ask() {
  if (!question.value.trim()) {
    showSuggestions.value = true;
    await loadSuggestions('');
    message.info('请先选择一个推荐问题，或输入你遇到的运维问题');
    return;
  }
  loading.value = true;
  showSuggestions.value = false;
  try {
    const result = await askQuestion(question.value);
    answer.value = result.answer;
    references.value = result.references || [];
    needHuman.value = result.need_human;
    modelStatus.value = result.model_status;
    if (result.employee) {
      llmStatus.value = {
        ...llmStatus.value,
        mode: 'llm',
        ready: true,
        employee_name: result.employee.name,
        employee_role: result.employee.role,
      };
    }
    await loadStats();
  } finally {
    loading.value = false;
  }
}

async function transferToHuman() {
  if (!question.value.trim()) {
    message.warning('请先输入问题描述，再创建在线记录');
    return;
  }
  creatingIssue.value = true;
  try {
    await createIssue({
      title: question.value.slice(0, 40),
      description: question.value,
      priority: needHuman.value ? 'high' : 'medium',
    });
    message.success('已创建在线记录，运维人员可在“在线记录”中处理和回访');
    await loadStats();
  } finally {
    creatingIssue.value = false;
  }
}

function useQuickAction(query: string) {
  question.value = query;
  showSuggestions.value = false;
  void ask();
}

watch(
  question,
  (value) => {
    showSuggestions.value = true;
    if (suggestTimer) clearTimeout(suggestTimer);
    suggestTimer = setTimeout(() => loadSuggestions(value), 180);
  },
);

onMounted(async () => {
  await Promise.all([loadStats(), loadLlmStatus(), loadSuggestions('')]);
});
</script>

<template>
  <div class="ops-page p-5">
    <div class="hero overflow-hidden rounded-3xl p-6 text-white md:p-8">
      <div class="hero-grid">
        <section class="min-w-0">
          <div class="eyebrow mb-3">AI + RAG 运维申告门户</div>
          <div class="mb-4 flex flex-wrap gap-2">
            <a-tag color="cyan">{{ llmStatus.employee_name || '云维' }} · {{ llmStatus.employee_role || '企业运维数字员工' }}</a-tag>
            <a-tag :color="llmStatus.ready ? 'green' : 'red'">
              {{ llmModeText }}
            </a-tag>
          </div>
          <h1 class="mb-3 text-3xl font-semibold md:text-4xl">运维数字员工</h1>
          <p class="mb-5 max-w-3xl text-base leading-7 text-white/80">
            先自助查询，无法解决时转人工；账号操作、问题处理、回访和知识沉淀形成闭环。
          </p>

          <div class="assistant-box">
            <a-textarea
              v-model:value="question"
              :auto-size="{ minRows: 4, maxRows: 8 }"
              class="assistant-input"
              placeholder="试试输入一个字，如“账”“密”“V”，或不输入直接查看推荐问题"
              @focus="showSuggestions = true"
              @press-enter.prevent="ask"
            />

            <div v-if="showSuggestions" class="suggest-panel">
              <div class="mb-3 flex items-center justify-between gap-3">
                <div>
                  <div class="font-medium text-slate-900">推荐查询</div>
                  <div class="text-xs text-slate-500">支持空输入推荐，也支持按关键字实时匹配知识库</div>
                </div>
                <a-tag v-if="suggestLoading">匹配中</a-tag>
              </div>
              <a-empty v-if="!visibleSuggestions.length && !suggestLoading" description="暂无匹配，请换个关键词" />
              <button
                v-for="item in visibleSuggestions"
                :key="item.id"
                class="suggest-item"
                type="button"
                @click="chooseSuggestion(item)"
              >
                <span class="min-w-0">
                  <strong>{{ item.query || item.title }}</strong>
                  <small>{{ item.title }} · {{ item.source_type }}</small>
                </span>
                <span class="tag-row">
                  <a-tag v-for="tag in item.tags" :key="tag">{{ tag }}</a-tag>
                </span>
              </button>
            </div>

            <div class="mt-4 flex flex-wrap gap-3">
              <a-button :loading="loading" size="large" type="primary" @click="ask">
                提交给数字员工
              </a-button>
              <a-button :loading="creatingIssue" size="large" @click="transferToHuman">
                创建在线记录 / 转人工
              </a-button>
              <a-button size="large" @click="loadSuggestions('')">刷新推荐</a-button>
            </div>
          </div>
        </section>

        <aside class="guide-card">
          <h2>使用指导</h2>
          <div v-for="step in guideSteps" :key="step.title" class="guide-step">
            <strong>{{ step.title }}</strong>
            <span>{{ step.text }}</span>
          </div>
        </aside>
      </div>
    </div>

    <div class="mt-5 grid gap-4 md:grid-cols-4">
      <a-card>
        <div class="text-gray-500">问答次数</div>
        <div class="mt-2 text-2xl font-semibold">{{ stats.total_qa || 0 }}</div>
      </a-card>
      <a-card>
        <div class="text-gray-500">自助解决率</div>
        <div class="mt-2 text-2xl font-semibold">
          {{ Math.round((stats.self_solved_rate || 0) * 100) }}%
        </div>
      </a-card>
      <a-card>
        <div class="text-gray-500">在线记录</div>
        <div class="mt-2 text-2xl font-semibold">{{ stats.issues || 0 }}</div>
      </a-card>
      <a-card>
        <div class="text-gray-500">知识条目</div>
        <div class="mt-2 text-2xl font-semibold">{{ stats.knowledge || 0 }}</div>
      </a-card>
    </div>

    <a-alert
      v-if="!hasQuestion && !answer"
      class="mt-5"
      message="新手提示"
      description="页面加载后会自动给出推荐查询；也可以输入任意关键字触发建议，选择后再提交给数字员工。"
      show-icon
      type="info"
    />

    <a-card v-if="answer" class="mt-5" title="数字员工回答">
      <p class="whitespace-pre-wrap leading-7">{{ answer }}</p>
      <div class="mt-3 flex flex-wrap gap-2">
        <a-tag :color="needHuman ? 'red' : 'green'">
          {{ needHuman ? '建议转人工' : '可自助处理' }}
        </a-tag>
        <a-tag>员工模式：LLM 数字员工</a-tag>
        <a-tag>模型状态：{{ modelStatus }}</a-tag>
        <a-tag v-for="item in references" :key="item.id" color="blue">
          {{ item.title }} · {{ Math.round((item.score || 0) * 100) }}%
        </a-tag>
      </div>
      <div class="mt-4 flex flex-wrap gap-3">
        <a-button :loading="creatingIssue" danger v-if="needHuman" @click="transferToHuman">
          按该问题创建在线记录
        </a-button>
        <a-button @click="showSuggestions = true">继续选择推荐问题</a-button>
      </div>
    </a-card>

    <div class="mt-5 grid gap-4 md:grid-cols-4">
      <button
        v-for="item in quickActions"
        :key="item.title"
        class="quick-card"
        type="button"
        @click="useQuickAction(item.query)"
      >
        <strong>{{ item.title }}</strong>
        <span>{{ item.desc }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.ops-page {
  --ops-blue: #1d4ed8;
  --ops-cyan: #0891b2;
  --ops-ink: #0f172a;
}

.hero {
  background:
    radial-gradient(circle at 88% 18%, rgb(45 212 191 / 45%), transparent 30%),
    radial-gradient(circle at 8% 92%, rgb(96 165 250 / 32%), transparent 34%),
    linear-gradient(135deg, #0f172a, #1e3a8a 52%, #0f766e);
  box-shadow: 0 24px 80px rgb(15 23 42 / 22%);
}

.hero-grid {
  display: grid;
  gap: 28px;
  grid-template-columns: minmax(0, 1fr) 320px;
}

.eyebrow {
  color: rgb(191 219 254);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.assistant-box,
.guide-card {
  border: 1px solid rgb(255 255 255 / 20%);
  border-radius: 24px;
  background: rgb(255 255 255 / 12%);
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 18%);
  padding: 18px;
}

.assistant-input :deep(textarea) {
  border: 0;
  border-radius: 18px;
  box-shadow: 0 16px 40px rgb(15 23 42 / 18%);
  font-size: 16px;
  line-height: 1.7;
  padding: 16px;
}

.suggest-panel {
  background: rgb(255 255 255 / 96%);
  border-radius: 18px;
  box-shadow: 0 18px 50px rgb(15 23 42 / 22%);
  color: var(--ops-ink);
  margin-top: 12px;
  padding: 14px;
}

.suggest-item {
  align-items: center;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  cursor: pointer;
  display: flex;
  gap: 14px;
  justify-content: space-between;
  margin-top: 8px;
  padding: 12px;
  text-align: left;
  transition: all 0.18s ease;
  width: 100%;
}

.suggest-item:hover {
  background: #eff6ff;
  border-color: #93c5fd;
  transform: translateY(-1px);
}

.suggest-item strong,
.quick-card strong {
  display: block;
  font-size: 15px;
}

.suggest-item small,
.quick-card span,
.guide-step span {
  color: #64748b;
  display: block;
  font-size: 12px;
  margin-top: 4px;
}

.tag-row {
  flex-shrink: 0;
  text-align: right;
}

.guide-card h2 {
  font-size: 20px;
  font-weight: 700;
  margin-bottom: 18px;
}

.guide-step {
  background: rgb(255 255 255 / 90%);
  border-radius: 18px;
  color: var(--ops-ink);
  margin-top: 12px;
  padding: 14px;
}

.quick-card {
  background: linear-gradient(180deg, #fff, #f8fafc);
  border: 1px solid #e2e8f0;
  border-radius: 18px;
  cursor: pointer;
  padding: 18px;
  text-align: left;
  transition: all 0.18s ease;
}

.quick-card:hover {
  border-color: #38bdf8;
  box-shadow: 0 18px 40px rgb(2 132 199 / 12%);
  transform: translateY(-2px);
}

@media (max-width: 900px) {
  .hero-grid {
    grid-template-columns: 1fr;
  }

  .tag-row {
    display: none;
  }
}
</style>
