<script lang="ts" setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';

import { Button as AButton, Progress as AProgress, Tag as ATag, message } from 'ant-design-vue';

import {
  createDemoSession,
  resetDemoSession,
  runDemoStep,
} from '#/api/ops';

const state = ref<any>({});
const loading = ref(false);
const autoRunning = ref(false);
const detailOpen = ref(false);
const detailEvent = ref({ detail: '', role: 'system', title: '' });
const userScrollRef = ref<HTMLElement | null>(null);
const agentScrollRef = ref<HTMLElement | null>(null);
const opsScrollRef = ref<HTMLElement | null>(null);
const adminScrollRef = ref<HTMLElement | null>(null);
const timelineScrollRef = ref<HTMLElement | null>(null);
let autoTimer: ReturnType<typeof setTimeout> | undefined;

const stepLabels: Record<string, string> = {
  agent_handoff: '人工协同',
  agent_review: '知识研判',
  audit_summary: '审计汇总',
  create_issue: '创建在线记录',
  knowledge_review: '知识审核',
  ops_accept: '运维接单',
  ops_assist: '处置建议',
  ops_handle: '运维处理',
  publish_knowledge: '知识发布',
  user_confirm: '用户确认',
  user_ask: '用户申告',
  visit_and_feedback: '回访评价',
};

const stepHints: Record<string, string> = {
  agent_handoff: '输出人工协同交接摘要，明确风险依据、已识别字段和待补充信息。',
  agent_review: '核对知识命中、置信度和风险等级，判断是否进入人工处理。',
  audit_summary: '汇总本次演示的审计日志、问答记录和闭环指标。',
  create_issue: '使用数字员工抽取字段生成在线记录，进入人工处理队列。',
  knowledge_review: '管理员复核处理案例，确认内容可沉淀为可检索知识。',
  ops_accept: '运维人员接单，核验影响范围、账号和证书状态。',
  ops_assist: '汇总处理建议、缺失字段、回访话术和知识引用。',
  ops_handle: '运维人员查看处置建议并提交处理结果。',
  publish_knowledge: '管理员发布处理案例，让后续同类问题可直接命中知识。',
  user_confirm: '用户确认 VPN 连接恢复，进入回访和满意度评价。',
  user_ask: '用户提交 VPN 证书过期诉求，数字员工先进行自助研判。',
  visit_and_feedback: '回访确认问题解决，沉淀待审核知识候选。',
};

const roleMeta: Record<string, { accent: string; label: string }> = {
  admin: { accent: 'amber', label: '管理员/审计员' },
  agent: { accent: 'cyan', label: '云维数字员工' },
  auditor: { accent: 'amber', label: '审计员' },
  ops: { accent: 'emerald', label: '运维人员' },
  system: { accent: 'slate', label: '系统' },
  user: { accent: 'sky', label: '普通用户' },
};

const statLabels: Record<string, string> = {
  audit_logs: '审计日志',
  closed_issues: '已关闭',
  issues: '在线记录',
  pending_knowledge: '待审知识',
  published_knowledge: '已发布知识',
  qa_logs: '问答记录',
};

const demoWindows = [
  { account: 'user / user123', fallback: '等待用户发起申告', path: '/portal', role: 'user', title: '普通用户门户' },
  { account: '云维 Agent', fallback: '等待知识检索与字段抽取', path: '/ops/dashboard', role: 'agent', title: '数字员工服务台' },
  { account: 'ops / ops123', fallback: '等待人工协同记录进入处理队列', path: '/ops/issues', role: 'ops', title: '运维处理台' },
  { account: 'admin / admin123', fallback: '等待知识候选和审计汇总', path: '/ops/demo', role: 'admin', title: '管理审计台' },
] as const;

const panels = computed(() => ({
  admin: state.value.admin_window || {},
  agent: state.value.agent_window || {},
  ops: state.value.ops_window || {},
  user: state.value.user_window || {},
}));

const steps = computed<string[]>(() => state.value.steps || []);
const timeline = computed<any[]>(() => state.value.timeline || []);

const latestEvents = computed<Record<string, any>>(() => {
  return timeline.value.reduce((acc, item) => {
    acc[item.role || 'system'] = item;
    return acc;
  }, {} as Record<string, any>);
});

const displayTimeline = computed(() => {
  const start = Math.max(timeline.value.length - 8, 0);
  return timeline.value.slice(start).map((item, index) => ({ ...item, displayIndex: start + index + 1 }));
});

const currentStage = computed(() => ({
  done: completedCount.value,
  label: currentStepLabel.value,
  total: steps.value.length,
}));

const currentEvent = computed(() => {
  return timeline.value[timeline.value.length - 1] || {
    detail: '等待演示链路初始化。',
    role: 'system',
    title: '流程准备中',
  };
});

const adminLatestEvent = computed(() => {
  return latestEvents.value.admin || latestEvents.value.auditor || {
    detail: '等待知识候选和审计汇总。',
    role: 'admin',
    title: '等待流转',
  };
});

const focusedRole = computed(() => {
  const role = currentEvent.value.role;
  if (role === 'auditor') return 'admin';
  if (['admin', 'agent', 'ops', 'user'].includes(role)) return role;
  return '';
});

const currentStepName = computed(() => {
  return steps.value[state.value.step_index] || 'finished';
});

const currentStepLabel = computed(() => {
  return stepLabels[currentStepName.value] || '已完成';
});

const currentStepHint = computed(() => {
  return stepHints[currentStepName.value] || '闭环演示已完成，可重置后再次播放。';
});

const progressPercent = computed(() => {
  if (!steps.value.length) return 0;
  return Math.round(((state.value.step_index || 0) / steps.value.length) * 100);
});

const isFinished = computed(() => state.value.status === 'finished');

const primaryActionText = computed(() => {
  if (isFinished.value) return '演示已完成';
  return `执行：${currentStepLabel.value}`;
});

const completedCount = computed(() => Math.min(state.value.step_index || 0, steps.value.length));

function shortScore(value: number) {
  return `${Math.round((value || 0) * 100)}%`;
}

function displayIndex(index: number | string) {
  return Number(index) + 1;
}

function roleLabel(role: string) {
  return roleMeta[role]?.label || role;
}

function roleClass(role: string) {
  return `role-${roleMeta[role]?.accent || 'slate'}`;
}

function statLabel(key: string | number) {
  return statLabels[String(key)] || String(key);
}

function latestFor(role: string, fallback: string) {
  return latestEvents.value[role] || { detail: fallback, role, title: '等待流转' };
}

function windowLatest(window: (typeof demoWindows)[number]) {
  return window.role === 'admin' ? adminLatestEvent.value : latestFor(window.role, window.fallback);
}

function isFocused(role: string) {
  return focusedRole.value === role;
}

function stepStatus(index: number) {
  if (index < (state.value.step_index || 0)) return 'done';
  if (index === (state.value.step_index || 0) && !isFinished.value) return 'active';
  return 'todo';
}

async function createSession() {
  loading.value = true;
  try {
    state.value = await createDemoSession();
    message.success('闭环演示已初始化');
  } catch (error: any) {
    message.error(error?.message || '初始化演示失败，请使用管理员账号登录');
  } finally {
    loading.value = false;
  }
}

async function resetSession() {
  stopAuto();
  if (!state.value.id) {
    await createSession();
    return;
  }
  loading.value = true;
  try {
    state.value = await resetDemoSession(state.value.id);
    message.success('演示链路已重置');
  } catch {
    await createSession();
  } finally {
    loading.value = false;
  }
}

async function runOneStep() {
  if (!state.value.id || isFinished.value || loading.value) return false;
  const runningLabel = currentStepLabel.value;
  loading.value = true;
  try {
    state.value = await runDemoStep(state.value.id);
    message.success(`已完成：${runningLabel}`);
    return true;
  } catch (error: any) {
    stopAuto();
    message.error(error?.message || '执行演示步骤失败');
    return false;
  } finally {
    loading.value = false;
  }
}

function scheduleNext() {
  if (!autoRunning.value || isFinished.value) {
    stopAuto();
    return;
  }
  autoTimer = setTimeout(async () => {
    await runOneStep();
    scheduleNext();
  }, 3500);
}

function startAuto() {
  if (!state.value.id || loading.value || isFinished.value) return;
  autoRunning.value = true;
  void runOneStep().then(scheduleNext);
}

function stopAuto() {
  autoRunning.value = false;
  if (autoTimer) clearTimeout(autoTimer);
}

function openEventDetail(event: any) {
  detailEvent.value = {
    detail: event?.detail || '暂无事件详情。',
    role: event?.role || 'system',
    title: event?.title || '事件详情',
  };
  detailOpen.value = true;
}

async function scrollLatestContentIntoView() {
  await nextTick();
  [userScrollRef.value, agentScrollRef.value, opsScrollRef.value, adminScrollRef.value, timelineScrollRef.value].forEach((el) => {
    if (el) el.scrollTop = el.scrollHeight;
  });
}

watch(() => state.value.step_index, scrollLatestContentIntoView);

onMounted(createSession);
onBeforeUnmount(stopAuto);
</script>

<template>
  <div
    class="demo-page"
    data-testid="demo-page"
    :data-status="state.status || 'initializing'"
    :data-step-index="state.step_index || 0"
    :data-step-total="steps.length"
  >
    <section class="hero-card stage-in" data-testid="demo-hero">
      <div class="hero-copy">
        <div class="eyebrow-row">
          <span class="live-dot"></span>
          <span>LIVE OPERATIONS WALKTHROUGH</span>
        </div>
        <h1>运维数字员工闭环指挥中心</h1>
        <p>
          以 VPN 证书过期为业务场景，同屏呈现用户申告、智能研判、运维处置、知识审核与审计留痕，展示从自助服务到知识沉淀的完整闭环。
        </p>
      </div>
      <div class="hero-console">
        <div>
          <span class="console-label">当前业务节点</span>
          <strong>{{ currentStepLabel }}</strong>
          <p>{{ currentStepHint }}</p>
        </div>
        <div class="stage-meter">
          <span>闭环进度</span>
          <b>{{ currentStage.done }}/{{ currentStage.total }}</b>
        </div>
        <div class="signal-grid" aria-label="演示能力状态">
          <span><b>RAG</b><small>证据命中</small></span>
          <span><b>Agent</b><small>工具协同</small></span>
          <span><b>Audit</b><small>全程留痕</small></span>
        </div>
        <div class="hero-actions" data-testid="demo-actions">
          <AButton data-testid="demo-reset" :loading="loading" size="large" @click="resetSession">重置链路</AButton>
          <AButton
            class="white-action-button"
            data-testid="demo-run-step"
            :disabled="isFinished || autoRunning"
            :loading="loading"
            size="large"
            :type="isFinished ? 'default' : 'primary'"
            @click="runOneStep"
          >
            {{ primaryActionText }}
          </AButton>
          <AButton
            v-if="!autoRunning"
            class="white-action-button"
            data-testid="demo-auto-run"
            :disabled="isFinished || loading"
            size="large"
            @click="startAuto"
          >
            自动推进
          </AButton>
          <AButton v-else danger data-testid="demo-pause-auto" size="large" @click="stopAuto">暂停推进</AButton>
        </div>
      </div>
    </section>

    <section class="progress-card stage-in stage-delay-1" data-testid="demo-progress">
      <div class="progress-layout">
        <div class="progress-flow">
          <div class="progress-head">
            <div>
              <span class="section-kicker">闭环链路</span>
              <strong>{{ state.prefix || '流程准备中' }}</strong>
            </div>
            <div class="progress-number">
              <b>{{ completedCount }}</b>
              <span>/ {{ steps.length }} 步完成</span>
            </div>
          </div>
          <AProgress :percent="progressPercent" :show-info="false" stroke-color="#0f766e" />
          <div class="step-list">
            <span
              v-for="(step, index) in steps"
              :key="step"
              :class="['step-pill', stepStatus(index)]"
            >
              <i>{{ displayIndex(index) }}</i>
              {{ stepLabels[step] || step }}
            </span>
          </div>
        </div>
        <div :class="['spotlight-card', roleClass(currentEvent.role)]">
          <span>关键事件聚焦</span>
          <strong>{{ currentEvent.title }}</strong>
          <p>{{ currentEvent.detail }}</p>
          <ATag>{{ roleLabel(currentEvent.role) }}</ATag>
          <button class="detail-link light" type="button" @click="openEventDetail(currentEvent)">查看完整事件</button>
        </div>
      </div>
    </section>

    <section class="demo-stage" data-testid="demo-stage">
      <div class="role-desktop">
        <div class="desktop-dock" aria-label="角色窗口切换">
          <button
            v-for="window in demoWindows"
            :key="window.role"
            :class="['dock-item', roleClass(window.role), { active: isFocused(window.role) }]"
            type="button"
            @click="openEventDetail(windowLatest(window))"
          >
            <span class="dock-light"></span>
            <strong>{{ window.title }}</strong>
            <small>{{ windowLatest(window).title }}</small>
          </button>
        </div>

        <div class="grid-panels">
          <article :class="['role-panel user-panel stage-in stage-delay-2', { focused: isFocused('user') }]" data-testid="demo-panel-user">
            <div class="window-chrome">
              <div class="window-controls" aria-hidden="true"><i></i><i></i><i></i></div>
              <div class="window-address">
                <strong>普通用户窗口</strong>
                <span>user / 门户</span>
              </div>
              <ATag :color="isFocused('user') ? 'blue' : 'default'">{{ isFocused('user') ? '当前焦点' : '在线' }}</ATag>
            </div>
            <div class="window-status role-sky">
              <span>用户申告</span>
              <strong>{{ latestFor('user', '等待用户发起申告').title }}</strong>
              <button class="detail-link" type="button" @click="openEventDetail(latestFor('user', '等待用户发起申告'))">详情</button>
            </div>
            <div class="question-card">
              <span>本次问题</span>
              <strong>{{ state.question }}</strong>
            </div>
            <div ref="userScrollRef" class="panel-scroll chat-feed">
              <div v-for="(item, index) in panels.user.messages || []" :key="index" :class="['chat-item', item.role]">
                <strong>{{ item.role === 'user' ? '用户' : '数字员工' }}</strong>
                <p>{{ item.content }}</p>
                <div v-if="item.references?.length" class="mini-tags">
                  <ATag v-for="ref in item.references" :key="ref.id" color="geekblue">
                    {{ ref.title }} · {{ shortScore(ref.score) }}
                  </ATag>
                </div>
              </div>
              <div v-if="!(panels.user.messages || []).length" class="empty-card">等待用户发起问题。</div>
            </div>
          </article>

        <article :class="['role-panel agent-panel stage-in stage-delay-3', { focused: isFocused('agent') }]" data-testid="demo-panel-agent">
          <div class="window-chrome">
            <div class="window-controls" aria-hidden="true"><i></i><i></i><i></i></div>
            <div class="window-address">
              <strong>数字员工窗口</strong>
              <span>云维 / 服务台</span>
            </div>
            <ATag :color="isFocused('agent') ? 'cyan' : 'default'">{{ isFocused('agent') ? '当前焦点' : '在线' }}</ATag>
          </div>
          <div class="window-status role-cyan">
            <span>智能研判</span>
            <strong>{{ latestFor('agent', '等待知识检索与字段抽取').title }}</strong>
            <button class="detail-link" type="button" @click="openEventDetail(latestFor('agent', '等待知识检索与字段抽取'))">详情</button>
          </div>
          <div class="agent-summary">
            <ATag color="processing">{{ panels.agent.model_status || '等待执行' }}</ATag>
            <ATag v-if="panels.agent.draft?.extraction_source" color="purple">
              字段抽取：{{ panels.agent.draft.extraction_source }}
            </ATag>
            <ATag v-if="panels.agent.decision?.risk_level" color="orange">
              风险：{{ panels.agent.decision.risk_level }}
            </ATag>
          </div>
          <div ref="agentScrollRef" class="panel-scroll">
            <ol class="trace-list">
              <li v-for="(step, index) in panels.agent.trace || []" :key="`${step.phase}-${step.tool}-${index}`">
                <strong>{{ step.phase }}</strong>
                <span>{{ step.tool }}</span>
                <p>{{ step.thought }}</p>
              </li>
            </ol>
            <div v-if="!(panels.agent.trace || []).length" class="empty-card">等待数字员工执行知识检索和字段抽取。</div>
            <div v-if="panels.agent.draft?.title" class="draft-box">
              <b>在线记录草案</b>
              <p>{{ panels.agent.draft.title }}</p>
              <div class="mini-tags">
                <ATag>{{ panels.agent.draft.category }}</ATag>
                <ATag>{{ panels.agent.draft.priority }}</ATag>
                <ATag v-if="panels.agent.draft.contact_phone" color="green">{{ panels.agent.draft.contact_phone }}</ATag>
                <ATag v-if="panels.agent.draft.impact_scope" color="geekblue">{{ panels.agent.draft.impact_scope }}</ATag>
              </div>
            </div>
          </div>
        </article>

        <article :class="['role-panel ops-panel stage-in stage-delay-4', { focused: isFocused('ops') }]" data-testid="demo-panel-ops">
          <div class="window-chrome">
            <div class="window-controls" aria-hidden="true"><i></i><i></i><i></i></div>
            <div class="window-address">
              <strong>运维窗口</strong>
              <span>ops / 处理台</span>
            </div>
            <ATag :color="isFocused('ops') ? 'green' : 'default'">{{ isFocused('ops') ? '当前焦点' : '在线' }}</ATag>
          </div>
          <div class="window-status role-emerald">
            <span>运维处理</span>
            <strong>{{ latestFor('ops', '等待人工协同记录进入处理队列').title }}</strong>
            <button class="detail-link" type="button" @click="openEventDetail(latestFor('ops', '等待人工协同记录进入处理队列'))">详情</button>
          </div>
          <div ref="opsScrollRef" class="panel-scroll">
            <div v-if="panels.ops.issue?.id" class="issue-card">
              <b>#{{ panels.ops.issue.id }} {{ panels.ops.issue.title }}</b>
              <div class="mini-tags">
                <ATag color="orange">{{ panels.ops.issue.status }}</ATag>
                <ATag v-if="panels.ops.issue.satisfaction_score" color="green">
                  满意度 {{ panels.ops.issue.satisfaction_score }} 分
                </ATag>
              </div>
            </div>
            <div v-else class="empty-card">创建在线记录后，这里会出现待处理记录。</div>
            <div v-if="panels.ops.assist?.summary" class="assist-box">
              <b>处置建议</b>
              <p>{{ panels.ops.assist.summary }}</p>
              <ul>
                <li v-for="item in (panels.ops.assist.suggested_steps || []).slice(0, 4)" :key="item">{{ item }}</li>
              </ul>
            </div>
            <div v-if="panels.ops.solution" class="solution-box">
              <b>处理结果</b>
              <p>{{ panels.ops.solution }}</p>
            </div>
          </div>
        </article>

        <article :class="['role-panel admin-panel stage-in stage-delay-5', { focused: isFocused('admin') }]" data-testid="demo-panel-admin">
          <div class="window-chrome">
            <div class="window-controls" aria-hidden="true"><i></i><i></i><i></i></div>
            <div class="window-address">
              <strong>管理审计窗口</strong>
              <span>admin / Demo</span>
            </div>
            <ATag :color="isFocused('admin') ? 'gold' : 'default'">{{ isFocused('admin') ? '当前焦点' : '在线' }}</ATag>
          </div>
          <div class="window-status role-amber">
            <span>知识审计</span>
            <strong>{{ adminLatestEvent.title }}</strong>
            <button class="detail-link" type="button" @click="openEventDetail(adminLatestEvent)">详情</button>
          </div>
          <div ref="adminScrollRef" class="panel-scroll">
            <div v-if="panels.admin.knowledge?.id" class="knowledge-card">
              <b>#{{ panels.admin.knowledge.id }} {{ panels.admin.knowledge.title }}</b>
              <ATag :color="panels.admin.knowledge.status === 'published' ? 'green' : 'orange'">
                {{ panels.admin.knowledge.status }}
              </ATag>
            </div>
            <div v-else class="empty-card">回访完成后会生成待审核知识候选。</div>
            <div class="stats-grid">
              <div v-for="(value, key) in panels.admin.stats || {}" :key="key">
                <strong>{{ value }}</strong>
                <span>{{ statLabel(key) }}</span>
              </div>
            </div>
            <ul class="audit-list">
              <li v-for="item in (panels.admin.audit || []).slice(0, 4)" :key="item.id">
                <span>{{ item.event_type }}</span>
                <p>{{ item.content }}</p>
              </li>
            </ul>
          </div>
        </article>
        </div>
      </div>

      <section class="timeline-card stage-in stage-delay-5" data-testid="demo-timeline">
        <div class="panel-head">
          <span>闭环时间线</span>
          <ATag :color="isFinished ? 'green' : 'blue'">{{ isFinished ? '已完成' : '演示中' }}</ATag>
        </div>
        <div ref="timelineScrollRef" class="timeline-list">
          <div
            v-for="(item, index) in displayTimeline"
            :key="`${item.title}-${index}`"
            :class="['timeline-item', roleClass(item.role)]"
          >
            <i>{{ item.displayIndex }}</i>
            <div>
              <strong>{{ item.title }}</strong>
              <p>{{ item.detail }}</p>
              <button class="detail-link" type="button" @click="openEventDetail(item)">完整事件</button>
            </div>
            <ATag>{{ roleLabel(item.role) }}</ATag>
          </div>
        </div>
      </section>
    </section>

    <a-modal v-model:open="detailOpen" :footer="null" width="720px">
      <div :class="['event-modal', roleClass(detailEvent.role)]">
        <span>{{ roleLabel(detailEvent.role) }}</span>
        <h3>{{ detailEvent.title }}</h3>
        <p>{{ detailEvent.detail }}</p>
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.demo-page {
  --demo-amber: #f59e0b;
  --demo-blue: #0f766e;
  --demo-border: rgb(15 23 42 / 9%);
  --demo-card: rgb(255 255 255 / 86%);
  --demo-ink: #102027;
  --demo-muted: #64748b;
  background:
    linear-gradient(120deg, rgb(20 184 166 / 9%) 0 1px, transparent 1px 68px),
    linear-gradient(150deg, #f8fafc 0%, #edf9f6 44%, #fff7ed 100%);
  color: var(--demo-ink);
  display: grid;
  gap: 14px;
  grid-template-rows: auto auto auto;
  min-height: max(720px, calc(100vh - 88px));
  overflow: auto;
  padding: 16px;
  position: relative;
}

.demo-page::before {
  background-image:
    linear-gradient(rgb(15 23 42 / 6%) 1px, transparent 1px),
    linear-gradient(90deg, rgb(15 23 42 / 6%) 1px, transparent 1px);
  background-size: 36px 36px;
  content: '';
  inset: 0;
  mask-image: linear-gradient(to bottom, #000, transparent 72%);
  pointer-events: none;
  position: absolute;
}

.hero-card,
.progress-card,
.role-panel,
.timeline-card {
  backdrop-filter: blur(22px);
  background: var(--demo-card);
  border: 1px solid var(--demo-border);
  border-radius: 16px;
  box-shadow: 0 18px 52px rgb(15 23 42 / 8%);
  position: relative;
  z-index: 1;
}

.hero-card {
  align-items: stretch;
  background:
    linear-gradient(90deg, rgb(255 255 255 / 82%), rgb(255 255 255 / 66%)),
    linear-gradient(135deg, rgb(20 184 166 / 14%), rgb(251 191 36 / 14%));
  display: grid;
  gap: 14px;
  grid-template-columns: minmax(0, 1.2fr) minmax(360px, 0.8fr);
  overflow: hidden;
  padding: 14px 16px;
}

.hero-card::after {
  background: linear-gradient(90deg, #38bdf8, #14b8a6, #f59e0b, #fb7185);
  content: '';
  height: 3px;
  inset: auto 0 0;
  position: absolute;
}

.eyebrow-row {
  align-items: center;
  color: var(--demo-blue);
  display: flex;
  font-size: 11px;
  font-weight: 900;
  gap: 8px;
  letter-spacing: 0.18em;
  margin-bottom: 8px;
  text-transform: uppercase;
}

.live-dot {
  background: #22c55e;
  border-radius: 50%;
  box-shadow: 0 0 0 8px rgb(34 197 94 / 12%);
  height: 9px;
  width: 9px;
}

.hero-card h1 {
  font-size: 34px;
  font-weight: 950;
  line-height: 1;
  margin: 0 0 8px;
  max-width: 760px;
}

.hero-card p,
.chat-item p,
.trace-list p,
.audit-list p,
.timeline-item p,
.assist-box p,
.solution-box p,
.draft-box p {
  color: var(--demo-muted);
  margin: 5px 0 0;
}

.hero-copy > p {
  font-size: 13px;
  line-height: 1.55;
  max-width: 820px;
}

.hero-console {
  background:
    linear-gradient(120deg, rgb(255 255 255 / 8%) 0 1px, transparent 1px 42px),
    linear-gradient(145deg, #0b1220, #113f3d 64%, #7c2d12);
  border: 1px solid rgb(255 255 255 / 12%);
  border-radius: 20px;
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 12%);
  color: #ecfeff;
  display: grid;
  gap: 8px;
  padding: 12px;
}

.console-label,
.section-kicker {
  color: rgb(148 163 184 / 95%);
  display: block;
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.hero-console strong {
  display: block;
  font-size: 20px;
  font-weight: 950;
  margin-top: 4px;
}

.hero-console p {
  color: rgb(204 251 241 / 78%);
  font-size: 13px;
}

.stage-meter {
  align-items: center;
  background: rgb(255 255 255 / 10%);
  border: 1px solid rgb(255 255 255 / 14%);
  border-radius: 16px;
  display: flex;
  justify-content: space-between;
  padding: 8px 10px;
}

.stage-meter span {
  color: rgb(204 251 241 / 78%);
  font-size: 12px;
  font-weight: 800;
}

.stage-meter b {
  color: #fff7ed;
  font-size: 18px;
}

.signal-grid {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.signal-grid span {
  background: rgb(255 255 255 / 9%);
  border: 1px solid rgb(255 255 255 / 13%);
  border-radius: 12px;
  min-width: 0;
  padding: 7px 8px;
}

.signal-grid b,
.signal-grid small {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.signal-grid b {
  color: #fff;
  font-size: 12px;
}

.signal-grid small {
  color: rgb(204 251 241 / 72%);
  font-size: 11px;
  margin-top: 2px;
}

.hero-actions,
.step-list,
.mini-tags,
.agent-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.hero-actions .white-action-button,
.hero-actions .white-action-button:disabled,
.hero-actions .white-action-button[disabled] {
  background: #fff;
  border-color: rgb(226 232 240 / 92%);
  color: #0f172a;
}

.hero-actions .white-action-button:hover:not(:disabled) {
  background: #f8fafc;
  border-color: #14b8a6;
  color: #0f766e;
}

.progress-card {
  background:
    linear-gradient(180deg, rgb(255 255 255 / 90%), rgb(255 255 255 / 72%));
  padding: 10px 14px;
}

.progress-layout {
  align-items: stretch;
  display: grid;
  gap: 14px;
  grid-template-columns: minmax(0, 1fr) minmax(320px, 0.36fr);
}

.progress-flow {
  min-width: 0;
  overflow: hidden;
}

.progress-head {
  align-items: flex-end;
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}

.progress-head strong {
  display: block;
  font-size: 14px;
  margin-top: 3px;
}

.progress-number {
  align-items: baseline;
  color: var(--demo-muted);
  display: flex;
  gap: 6px;
}

.progress-number b {
  color: var(--demo-blue);
  font-size: 26px;
  line-height: 1;
}

.step-list {
  margin-top: 10px;
}

.spotlight-card {
  background:
    linear-gradient(120deg, rgb(255 255 255 / 8%) 0 1px, transparent 1px 44px),
    linear-gradient(135deg, #0f172a, #134e4a);
  border-radius: 18px;
  color: #fff;
  min-height: 108px;
  isolation: isolate;
  padding: 12px 14px;
  position: relative;
  overflow: hidden;
}

.spotlight-card::before {
  background: linear-gradient(135deg, transparent 0 35%, rgb(255 255 255 / 14%) 35% 52%, transparent 52%);
  content: '';
  inset: 0;
  position: absolute;
  z-index: -1;
}

.spotlight-card span {
  color: rgb(204 251 241 / 82%);
  display: block;
  font-size: 11px;
  font-weight: 950;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.spotlight-card strong {
  display: block;
  font-size: 17px;
  margin-top: 4px;
  position: relative;
}

.spotlight-card p {
  color: rgb(255 255 255 / 82%);
  font-size: 13px;
  line-height: 1.55;
  margin: 6px 0 8px;
  position: relative;
}

.spotlight-card :deep(.ant-tag) {
  position: relative;
}

.spotlight-card.role-sky { background: linear-gradient(135deg, #075985, #0f766e); }
.spotlight-card.role-cyan { background: linear-gradient(135deg, #164e63, #115e59); }
.spotlight-card.role-emerald { background: linear-gradient(135deg, #064e3b, #166534); }
.spotlight-card.role-amber { background: linear-gradient(135deg, #78350f, #854d0e); }
.spotlight-card.role-slate { background: linear-gradient(135deg, #0f172a, #334155); }

.step-pill {
  align-items: center;
  background: #f1f5f9;
  border: 1px solid transparent;
  border-radius: 999px;
  color: var(--demo-muted);
  display: inline-flex;
  font-size: 11px;
  font-weight: 800;
  gap: 6px;
  padding: 5px 8px;
}

.step-pill i {
  border-radius: 50%;
  display: grid;
  font-style: normal;
  height: 18px;
  place-items: center;
  width: 18px;
}

.step-pill.done {
  background: #ccfbf1;
  color: #0f766e;
}

.step-pill.done i {
  background: #0f766e;
  color: white;
}

.step-pill.active {
  background: #fffbeb;
  border-color: rgb(245 158 11 / 32%);
  color: #92400e;
  transform: translateY(-1px);
}

.step-pill.active i {
  background: var(--demo-amber);
  color: white;
}

.demo-stage {
  display: grid;
  gap: 12px;
  grid-template-columns: minmax(0, 1fr) 360px;
  min-height: 760px;
  position: relative;
  z-index: 1;
}

.role-desktop {
  background:
    linear-gradient(120deg, rgb(15 23 42 / 7%) 0 1px, transparent 1px 48px),
    linear-gradient(135deg, rgb(15 23 42 / 7%), rgb(15 118 110 / 9%)),
    rgb(255 255 255 / 36%);
  border: 1px solid rgb(15 23 42 / 8%);
  border-radius: 22px;
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 64%);
  display: grid;
  gap: 10px;
  grid-template-rows: auto minmax(0, 1fr);
  min-height: 760px;
  padding: 10px;
}

.desktop-dock {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.dock-item {
  align-items: center;
  background: rgb(255 255 255 / 72%);
  border: 1px solid rgb(15 23 42 / 10%);
  border-radius: 14px;
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 70%);
  cursor: pointer;
  display: grid;
  gap: 3px 8px;
  grid-template-columns: auto minmax(0, 1fr);
  min-width: 0;
  padding: 8px 10px;
  text-align: left;
  transition: all 0.18s ease;
}

.dock-item strong,
.dock-item small {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dock-item strong {
  color: #0f172a;
  font-size: 12px;
  font-weight: 950;
}

.dock-item small {
  color: var(--demo-muted);
  font-size: 11px;
  grid-column: 2;
}

.dock-light {
  background: #94a3b8;
  border-radius: 50%;
  box-shadow: 0 0 0 4px rgb(148 163 184 / 14%);
  height: 9px;
  width: 9px;
}

.dock-item:hover,
.dock-item.active {
  background: #fff;
  box-shadow: 0 12px 28px rgb(15 23 42 / 10%);
  transform: translateY(-1px);
}

.dock-item.role-sky.active { border-color: #38bdf8; }
.dock-item.role-cyan.active { border-color: #22d3ee; }
.dock-item.role-emerald.active { border-color: #34d399; }
.dock-item.role-amber.active { border-color: #f59e0b; }
.dock-item.role-sky .dock-light { background: #38bdf8; }
.dock-item.role-cyan .dock-light { background: #22d3ee; }
.dock-item.role-emerald .dock-light { background: #34d399; }
.dock-item.role-amber .dock-light { background: #f59e0b; }

.grid-panels {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  grid-template-rows: repeat(2, minmax(340px, 1fr));
  min-height: 0;
}

.role-panel,
.timeline-card {
  min-height: 0;
  overflow: hidden;
  padding: 10px;
}

.role-panel {
  background:
    linear-gradient(180deg, rgb(255 255 255 / 92%), rgb(255 255 255 / 76%));
  display: flex;
  flex-direction: column;
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    transform 0.18s ease;
}

.role-panel::before {
  border-radius: 18px 18px 0 0;
  content: '';
  height: 5px;
  inset: 0 0 auto;
  position: absolute;
}

.user-panel::before { background: linear-gradient(90deg, #38bdf8, #0ea5e9); }
.agent-panel::before { background: linear-gradient(90deg, #22d3ee, #14b8a6); }
.ops-panel::before { background: linear-gradient(90deg, #34d399, #16a34a); }
.admin-panel::before { background: linear-gradient(90deg, #fbbf24, #f97316); }

.role-panel.focused {
  border-color: rgb(15 118 110 / 36%);
  box-shadow:
    0 22px 58px rgb(15 23 42 / 16%),
    0 0 0 3px rgb(20 184 166 / 12%);
  transform: translateY(-2px);
}

.window-chrome {
  align-items: center;
  background:
    linear-gradient(120deg, rgb(255 255 255 / 8%) 0 1px, transparent 1px 38px),
    linear-gradient(135deg, #0b1220, #1f2937);
  border: 1px solid rgb(255 255 255 / 10%);
  border-radius: 14px;
  box-shadow: 0 10px 24px rgb(15 23 42 / 12%);
  color: #e2e8f0;
  display: grid;
  flex: 0 0 auto;
  gap: 10px;
  grid-template-columns: auto minmax(0, 1fr) auto;
  margin-bottom: 6px;
  min-height: 40px;
  padding: 7px 9px;
}

.window-controls {
  display: flex;
  gap: 5px;
}

.window-controls i {
  border-radius: 50%;
  display: block;
  height: 10px;
  width: 10px;
}

.window-controls i:nth-child(1) { background: #fb7185; }
.window-controls i:nth-child(2) { background: #fbbf24; }
.window-controls i:nth-child(3) { background: #34d399; }

.window-address {
  min-width: 0;
}

.window-address strong,
.window-address span {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.window-address strong {
  color: #f8fafc;
  font-size: 12px;
  font-weight: 950;
  line-height: 1.2;
  overflow: visible;
  text-overflow: clip;
  white-space: normal;
}

.window-address span {
  color: rgb(203 213 225 / 82%);
  font-size: 11px;
  margin-top: 1px;
}

.panel-head {
  align-items: center;
  display: flex;
  flex: 0 0 auto;
  justify-content: space-between;
  margin-bottom: 4px;
  min-height: 24px;
}

.panel-head span {
  color: var(--demo-blue);
  font-size: 12px;
  font-weight: 950;
  letter-spacing: 0.08em;
  line-height: 1.25;
  white-space: nowrap;
}

.window-status {
  align-items: start;
  border: 1px solid rgb(15 23 42 / 9%);
  border-radius: 10px;
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 80%);
  display: grid;
  flex: 0 0 auto;
  gap: 8px;
  grid-template-columns: auto minmax(0, 1fr) auto;
  margin-bottom: 6px;
  min-height: 46px;
  padding: 6px 8px;
}

.window-status span {
  border-radius: 999px;
  color: #fff;
  display: inline-flex;
  font-size: 11px;
  font-weight: 950;
  line-height: 1;
  padding: 5px 8px;
  white-space: nowrap;
}

.window-status strong {
  color: #0f172a;
  display: block;
  font-size: 12px;
  line-height: 1.35;
  min-width: 0;
  overflow-wrap: anywhere;
}

.window-status.role-sky span { background: #0284c7; }
.window-status.role-cyan span { background: #0891b2; }
.window-status.role-emerald span { background: #059669; }
.window-status.role-amber span { background: #d97706; }

.window-status.role-sky { background: linear-gradient(135deg, #eff6ff, #f0f9ff); }
.window-status.role-cyan { background: linear-gradient(135deg, #ecfeff, #f0fdfa); }
.window-status.role-emerald { background: linear-gradient(135deg, #ecfdf5, #f7fee7); }
.window-status.role-amber { background: linear-gradient(135deg, #fffbeb, #fff7ed); }

.window-status .detail-link {
  font-size: 11px;
}

.detail-link {
  background: transparent;
  border: 0;
  color: #0f766e;
  cursor: pointer;
  flex: 0 0 auto;
  font-size: 11px;
  font-weight: 900;
  padding: 0;
}

.detail-link.light {
  color: #ccfbf1;
  display: block;
  margin-top: 6px;
  position: relative;
}

.question-card,
.draft-box,
.issue-card,
.assist-box,
.solution-box,
.knowledge-card,
.empty-card {
  background: linear-gradient(180deg, #fff, #f8fafc);
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  box-shadow: 0 8px 24px rgb(15 23 42 / 4%);
  margin-bottom: 8px;
  padding: 8px;
}

.question-card,
.agent-summary {
  flex: 0 0 auto;
}

.question-card span {
  color: var(--demo-muted);
  display: block;
  font-size: 11px;
  font-weight: 900;
  margin-bottom: 4px;
}

.question-card strong,
.issue-card b,
.knowledge-card b,
.draft-box b,
.assist-box b,
.solution-box b {
  color: #0f172a;
}

.chat-feed,
.trace-list,
.audit-list,
.timeline-list {
  display: grid;
  gap: 7px;
}

.panel-scroll,
.timeline-list {
  flex: 1;
  max-height: 100%;
  min-height: 0;
  overflow: auto;
  padding-right: 2px;
  scroll-behavior: smooth;
  scrollbar-width: thin;
}

.chat-item,
.trace-list li,
.audit-list li,
.timeline-item {
  background: rgb(255 255 255 / 86%);
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  box-shadow: 0 8px 22px rgb(15 23 42 / 4%);
  padding: 8px;
}

.chat-item p,
.trace-list p,
.audit-list p,
.timeline-item p,
.assist-box p,
.solution-box p,
.draft-box p {
  overflow-wrap: anywhere;
}

.chat-item.user {
  border-color: #bae6fd;
  box-shadow: inset 4px 0 0 #38bdf8;
}

.chat-item.assistant {
  border-color: #ccfbf1;
  box-shadow: inset 4px 0 0 #14b8a6;
}

.empty-card {
  color: var(--demo-muted);
  font-weight: 700;
  margin-bottom: 0;
}

.trace-list {
  list-style: none;
  margin: 0 0 8px;
  padding: 0;
}

.trace-list li {
  align-items: start;
  display: grid;
  gap: 6px;
  grid-template-columns: 52px 82px minmax(0, 1fr);
}

.trace-list strong {
  color: #0f766e;
}

.trace-list span {
  color: #a16207;
  font-weight: 900;
}

.assist-box ul,
.audit-list {
  margin: 8px 0 0;
  padding-left: 18px;
}

.stats-grid {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 8px 0;
}

.stats-grid div {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 8px;
}

.stats-grid strong,
.stats-grid span {
  display: block;
}

.stats-grid strong {
  font-size: 18px;
  font-weight: 950;
}

.stats-grid span {
  color: var(--demo-muted);
  font-size: 11px;
}

.timeline-card {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
}

.timeline-item {
  align-items: start;
  display: grid;
  gap: 9px;
  grid-template-columns: 28px minmax(0, 1fr);
}

.timeline-item :deep(.ant-tag) {
  grid-column: 2;
  justify-self: start;
}

.timeline-item i {
  background: #0f766e;
  border-radius: 50%;
  color: white;
  display: grid;
  font-style: normal;
  font-weight: 950;
  height: 24px;
  place-items: center;
  width: 24px;
}

.event-modal {
  border-left: 5px solid #0f766e;
  padding: 4px 0 4px 16px;
}

.event-modal span {
  color: var(--demo-muted);
  display: block;
  font-size: 12px;
  font-weight: 900;
  margin-bottom: 8px;
}

.event-modal h3 {
  color: #0f172a;
  font-size: 22px;
  font-weight: 900;
  margin: 0 0 10px;
}

.event-modal p {
  color: #334155;
  line-height: 1.8;
  margin: 0;
  white-space: pre-wrap;
}

.event-modal.role-sky { border-color: #0284c7; }
.event-modal.role-cyan { border-color: #0891b2; }
.event-modal.role-emerald { border-color: #059669; }
.event-modal.role-amber { border-color: #d97706; }
.event-modal.role-slate { border-color: #475569; }

.timeline-item.role-sky i { background: #0284c7; }
.timeline-item.role-cyan i { background: #0891b2; }
.timeline-item.role-emerald i { background: #059669; }
.timeline-item.role-amber i { background: #d97706; }
.timeline-item.role-slate i { background: #475569; }

.stage-in {
  animation: stage-in 0.55s ease both;
}

.stage-delay-1 { animation-delay: 0.08s; }
.stage-delay-2 { animation-delay: 0.14s; }
.stage-delay-3 { animation-delay: 0.2s; }
.stage-delay-4 { animation-delay: 0.26s; }
.stage-delay-5 { animation-delay: 0.32s; }

@keyframes stage-in {
  from {
    opacity: 0;
    transform: translateY(16px) scale(0.99);
  }

  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

/* Command-center visual pass. The demo is the recording surface, so it gets a
   stronger cockpit treatment than the routine CRUD pages. */
.demo-page {
  --demo-border: rgb(148 163 184 / 18%);
  --demo-card: rgb(8 20 31 / 82%);
  --demo-ink: #eaf6f5;
  --demo-muted: #9fb3c8;
  background:
    linear-gradient(115deg, rgb(34 211 238 / 12%) 0 1px, transparent 1px 86px),
    linear-gradient(155deg, #050b12 0%, #071923 46%, #1a1110 100%);
}

.demo-page::before {
  background-image:
    linear-gradient(rgb(125 211 252 / 8%) 1px, transparent 1px),
    linear-gradient(90deg, rgb(125 211 252 / 7%) 1px, transparent 1px);
}

.hero-card,
.progress-card,
.role-panel,
.timeline-card {
  background:
    linear-gradient(180deg, rgb(15 23 42 / 86%), rgb(8 20 31 / 82%));
  border-color: rgb(148 163 184 / 18%);
  box-shadow:
    0 24px 80px rgb(0 0 0 / 28%),
    inset 0 1px 0 rgb(255 255 255 / 8%);
}

.hero-card {
  background:
    linear-gradient(120deg, rgb(45 212 191 / 14%) 0 1px, transparent 1px 54px),
    linear-gradient(135deg, rgb(15 23 42 / 92%), rgb(6 78 59 / 72) 58%, rgb(69 26 3 / 78));
}

.hero-card h1,
.progress-head strong,
.question-card strong,
.issue-card b,
.knowledge-card b,
.draft-box b,
.assist-box b,
.solution-box b,
.window-status strong,
.dock-item strong {
  color: #f8fafc;
}

.hero-copy > p,
.progress-number,
.chat-item p,
.trace-list p,
.audit-list p,
.timeline-item p,
.assist-box p,
.solution-box p,
.draft-box p {
  color: var(--demo-muted);
}

.progress-card {
  background:
    linear-gradient(90deg, rgb(45 212 191 / 10%) 0 1px, transparent 1px 56px),
    rgb(8 20 31 / 86%);
}

.section-kicker {
  color: #67e8f9;
}

.progress-number b {
  color: #5eead4;
}

.step-pill {
  background: rgb(15 23 42 / 78%);
  border-color: rgb(148 163 184 / 14%);
  color: #a8bdd1;
}

.step-pill.done {
  background: rgb(20 184 166 / 16%);
  color: #99f6e4;
}

.step-pill.active {
  background: rgb(245 158 11 / 17%);
  box-shadow: 0 0 0 3px rgb(245 158 11 / 10%);
  color: #fde68a;
}

.demo-stage {
  isolation: isolate;
}

.demo-stage::before {
  background: linear-gradient(90deg, transparent, rgb(34 211 238 / 24%), rgb(245 158 11 / 18%), transparent);
  content: '';
  height: 1px;
  inset: 38px 390px auto 26px;
  opacity: 0.88;
  position: absolute;
  z-index: 0;
}

.role-desktop {
  background:
    linear-gradient(120deg, rgb(125 211 252 / 8%) 0 1px, transparent 1px 58px),
    linear-gradient(145deg, rgb(2 8 23 / 94%), rgb(8 47 73 / 52));
  border-color: rgb(125 211 252 / 18%);
  box-shadow:
    inset 0 1px 0 rgb(255 255 255 / 9%),
    0 26px 80px rgb(0 0 0 / 26%);
  overflow: hidden;
  position: relative;
}

.role-desktop::before {
  animation: command-scan 7s linear infinite;
  background: linear-gradient(90deg, transparent, rgb(103 232 249 / 13%), transparent);
  content: '';
  height: 100%;
  left: -28%;
  pointer-events: none;
  position: absolute;
  top: 0;
  transform: skewX(-18deg);
  width: 28%;
  z-index: 0;
}

.desktop-dock,
.grid-panels {
  position: relative;
  z-index: 1;
}

.dock-item {
  background: rgb(15 23 42 / 78%);
  border-color: rgb(148 163 184 / 18%);
}

.dock-item small {
  color: #94a3b8;
}

.dock-item:hover,
.dock-item.active {
  background: rgb(8 47 73 / 86%);
  box-shadow:
    0 14px 34px rgb(0 0 0 / 22%),
    0 0 0 1px rgb(103 232 249 / 18%);
}

.role-panel {
  background:
    linear-gradient(180deg, rgb(15 23 42 / 90%), rgb(8 20 31 / 86%));
}

.role-panel.focused {
  border-color: rgb(94 234 212 / 48%);
  box-shadow:
    0 24px 70px rgb(0 0 0 / 34%),
    0 0 0 3px rgb(45 212 191 / 13%);
}

.window-status.role-sky,
.window-status.role-cyan,
.window-status.role-emerald,
.window-status.role-amber {
  background: rgb(15 23 42 / 72%);
  border-color: rgb(148 163 184 / 16%);
}

.question-card,
.draft-box,
.issue-card,
.assist-box,
.solution-box,
.knowledge-card,
.empty-card,
.chat-item,
.trace-list li,
.audit-list li,
.timeline-item,
.stats-grid div {
  background: rgb(15 23 42 / 66%);
  border-color: rgb(148 163 184 / 16%);
}

.empty-card,
.question-card span,
.stats-grid span {
  color: #94a3b8;
}

.trace-list strong {
  color: #5eead4;
}

.trace-list span {
  color: #fde68a;
}

.timeline-card {
  background:
    linear-gradient(180deg, rgb(15 23 42 / 90%), rgb(8 20 31 / 82%));
}

.panel-head span,
.detail-link {
  color: #67e8f9;
}

.event-modal {
  background: #fff;
}

@keyframes command-scan {
  from {
    left: -30%;
  }

  to {
    left: 112%;
  }
}

@media (max-width: 1180px) {
  .demo-page {
    height: auto;
    min-height: 100%;
    overflow: auto;
  }

  .hero-card,
  .demo-stage,
  .progress-layout,
  .grid-panels {
    grid-template-columns: 1fr;
  }

  .grid-panels {
    grid-template-rows: none;
  }

  .role-panel,
  .timeline-card {
    min-height: 320px;
  }
}

@media (max-width: 720px) {
  .demo-page {
    padding: 14px;
  }

  .hero-card h1 {
    font-size: 28px;
  }

  .hero-card,
  .progress-card,
  .role-panel,
  .timeline-card {
    border-radius: 18px;
    padding: 16px;
  }

  .progress-head,
  .timeline-item,
  .trace-list li {
    align-items: flex-start;
    grid-template-columns: 1fr;
  }

  .progress-head {
    display: grid;
    gap: 12px;
  }

  .stats-grid {
    grid-template-columns: 1fr 1fr;
  }

  .desktop-dock {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
