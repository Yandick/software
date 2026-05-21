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
  <div class="demo-page">
    <section class="hero-card stage-in">
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
        <div class="hero-actions">
          <AButton :loading="loading" size="large" @click="resetSession">重置链路</AButton>
          <AButton
            class="white-action-button"
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
            :disabled="isFinished || loading"
            size="large"
            @click="startAuto"
          >
            自动推进
          </AButton>
          <AButton v-else danger size="large" @click="stopAuto">暂停推进</AButton>
        </div>
      </div>
    </section>

    <section class="progress-card stage-in stage-delay-1">
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

    <section class="demo-stage">
      <div class="grid-panels">
        <article class="role-panel user-panel stage-in stage-delay-2">
          <div class="panel-head">
            <span>用户窗口</span>
            <ATag color="blue">普通用户</ATag>
          </div>
          <h2>申告与状态感知</h2>
          <div class="live-summary role-sky">
            <small>最新动态</small>
            <div class="summary-title-row">
              <strong>{{ latestFor('user', '等待用户发起申告').title }}</strong>
              <button class="detail-link" type="button" @click="openEventDetail(latestFor('user', '等待用户发起申告'))">完整</button>
            </div>
            <p>{{ latestFor('user', '等待用户发起申告').detail }}</p>
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

        <article class="role-panel agent-panel stage-in stage-delay-3">
          <div class="panel-head">
            <span>数字员工窗口</span>
            <ATag color="cyan">云维数字员工</ATag>
          </div>
          <h2>研判与工具调用</h2>
          <div class="live-summary role-cyan">
            <small>最新动态</small>
            <div class="summary-title-row">
              <strong>{{ latestFor('agent', '等待知识检索与字段抽取').title }}</strong>
              <button class="detail-link" type="button" @click="openEventDetail(latestFor('agent', '等待知识检索与字段抽取'))">完整</button>
            </div>
            <p>{{ latestFor('agent', '等待知识检索与字段抽取').detail }}</p>
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

        <article class="role-panel ops-panel stage-in stage-delay-4">
          <div class="panel-head">
            <span>运维窗口</span>
            <ATag color="green">运维人员</ATag>
          </div>
          <h2>处理辅助与回访</h2>
          <div class="live-summary role-emerald">
            <small>最新动态</small>
            <div class="summary-title-row">
              <strong>{{ latestFor('ops', '等待人工协同记录进入处理队列').title }}</strong>
              <button class="detail-link" type="button" @click="openEventDetail(latestFor('ops', '等待人工协同记录进入处理队列'))">完整</button>
            </div>
            <p>{{ latestFor('ops', '等待人工协同记录进入处理队列').detail }}</p>
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

        <article class="role-panel admin-panel stage-in stage-delay-5">
          <div class="panel-head">
            <span>管理/审计窗口</span>
            <ATag color="gold">管理员 / 审计员</ATag>
          </div>
          <h2>知识发布与审计</h2>
          <div class="live-summary role-amber">
            <small>最新动态</small>
            <div class="summary-title-row">
              <strong>{{ adminLatestEvent.title }}</strong>
              <button class="detail-link" type="button" @click="openEventDetail(adminLatestEvent)">完整</button>
            </div>
            <p>{{ adminLatestEvent.detail }}</p>
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

      <section class="timeline-card stage-in stage-delay-5">
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
  --demo-border: rgb(15 23 42 / 10%);
  --demo-card: rgb(255 255 255 / 90%);
  --demo-ink: #102027;
  --demo-muted: #64748b;
  background:
    radial-gradient(circle at 8% 2%, rgb(45 212 191 / 34%), transparent 30%),
    radial-gradient(circle at 88% 0%, rgb(251 191 36 / 28%), transparent 28%),
    linear-gradient(135deg, #ecfeff 0%, #f8fafc 42%, #fff7ed 100%);
  color: var(--demo-ink);
  display: grid;
  gap: 12px;
  grid-template-rows: auto auto minmax(0, 1fr);
  height: calc(100vh - 88px);
  min-height: 720px;
  overflow: hidden;
  padding: 14px;
  position: relative;
}

.demo-page::before {
  background-image:
    linear-gradient(rgb(15 118 110 / 7%) 1px, transparent 1px),
    linear-gradient(90deg, rgb(15 118 110 / 7%) 1px, transparent 1px);
  background-size: 34px 34px;
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
  border-radius: 18px;
  box-shadow: 0 18px 50px rgb(15 23 42 / 9%);
  position: relative;
  z-index: 1;
}

.hero-card {
  align-items: stretch;
  display: grid;
  gap: 14px;
  grid-template-columns: minmax(0, 1.2fr) minmax(360px, 0.8fr);
  padding: 14px 16px;
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
  background: linear-gradient(145deg, #0f172a, #134e4a 64%, #78350f);
  border-radius: 20px;
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
  background: linear-gradient(135deg, #0f172a, #134e4a);
  border-radius: 18px;
  color: #fff;
  min-height: 108px;
  padding: 12px 14px;
  position: relative;
  overflow: hidden;
}

.spotlight-card::before {
  background: radial-gradient(circle, rgb(255 255 255 / 22%), transparent 62%);
  content: '';
  height: 180px;
  position: absolute;
  right: -70px;
  top: -80px;
  width: 180px;
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
  min-height: 0;
  position: relative;
  z-index: 1;
}

.grid-panels {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  grid-template-rows: repeat(2, minmax(0, 1fr));
  min-height: 0;
}

.role-panel,
.timeline-card {
  min-height: 0;
  overflow: hidden;
  padding: 12px;
}

.role-panel {
  display: flex;
  flex-direction: column;
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

.panel-head {
  align-items: center;
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
}

.panel-head span {
  color: var(--demo-blue);
  font-size: 12px;
  font-weight: 950;
  letter-spacing: 0.08em;
}

.role-panel h2 {
  font-size: 17px;
  font-weight: 950;
  margin: 0 0 10px;
}

.live-summary {
  border: 1px solid rgb(15 23 42 / 9%);
  border-radius: 12px;
  margin-bottom: 7px;
  padding: 8px;
}

.live-summary small {
  color: var(--demo-muted);
  display: block;
  font-size: 10px;
  font-weight: 950;
  letter-spacing: 0.12em;
  margin-bottom: 4px;
  text-transform: uppercase;
}

.live-summary strong {
  color: #0f172a;
  display: block;
  font-size: 14px;
  min-width: 0;
}

.live-summary p {
  color: #475569;
  font-size: 11.5px;
  line-height: 1.45;
  margin: 4px 0 0;
  overflow-wrap: anywhere;
}

.summary-title-row {
  align-items: start;
  display: flex;
  gap: 8px;
  justify-content: space-between;
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

.live-summary.role-sky { background: linear-gradient(135deg, #eff6ff, #f0f9ff); }
.live-summary.role-cyan { background: linear-gradient(135deg, #ecfeff, #f0fdfa); }
.live-summary.role-emerald { background: linear-gradient(135deg, #ecfdf5, #f7fee7); }
.live-summary.role-amber { background: linear-gradient(135deg, #fffbeb, #fff7ed); }

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
  margin-bottom: 8px;
  padding: 8px;
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
}
</style>
