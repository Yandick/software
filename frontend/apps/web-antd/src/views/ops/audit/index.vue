<script lang="ts" setup>
import { computed, onMounted, ref } from 'vue';

import { getAuditLogs, getStats } from '#/api/ops';

const stats = ref<any>({});
const auditLogs = ref<any[]>([]);
const qaLogs = ref<any[]>([]);
const eventSummary = ref<any[]>([]);
const targetSummary = ref<any[]>([]);
const loading = ref(false);
const filters = ref({
  event_type: '',
  limit: 100,
  need_human: '',
  q: '',
  target_type: '',
});

const auditColumns = [
  { dataIndex: 'event_type', title: '事件类型', width: 160 },
  { dataIndex: 'target_type', title: '对象', width: 120 },
  { dataIndex: 'target_id', title: '对象ID', width: 90 },
  { dataIndex: 'content', title: '内容' },
  { dataIndex: 'created_at', title: '时间', width: 190 },
];

const qaColumns = [
  { dataIndex: 'question', title: '用户问题', width: 260 },
  { dataIndex: 'answer', title: '数字员工回答' },
  { dataIndex: 'need_human', title: '人工协同', width: 110 },
  { dataIndex: 'model_status', title: '模型状态', width: 120 },
  { dataIndex: 'created_at', title: '时间', width: 190 },
];

const eventOptions = computed(() =>
  eventSummary.value.map((item) => ({
    label: `${item.event_type} (${item.count})`,
    value: item.event_type,
  })),
);

const targetOptions = computed(() =>
  targetSummary.value.map((item) => ({
    label: `${item.target_type} (${item.count})`,
    value: item.target_type,
  })),
);

const metricCards = computed(() => [
  { label: '问答次数', value: stats.value.total_qa || 0, tone: 'blue' },
  { label: '人工协同率', value: `${Math.round((stats.value.human_transfer_rate || 0) * 100)}%`, tone: 'orange' },
  { label: '待处理记录', value: stats.value.pending_issues || 0, tone: 'red' },
  { label: '已关闭记录', value: stats.value.closed_issues || 0, tone: 'green' },
  { label: '活跃账号', value: stats.value.active_accounts || 0, tone: 'cyan' },
  { label: '冻结账号', value: stats.value.frozen_accounts || 0, tone: 'gray' },
  { label: '已发布知识', value: stats.value.published_knowledge || 0, tone: 'green' },
  { label: '待审核知识', value: stats.value.pending_knowledge || 0, tone: 'orange' },
]);

async function load() {
  loading.value = true;
  try {
    const params = Object.fromEntries(
      Object.entries(filters.value).filter(([, value]) => value !== '' && value !== undefined && value !== null),
    );
    const [statsResult, logsResult] = await Promise.all([getStats(), getAuditLogs(params)]);
    stats.value = statsResult;
    auditLogs.value = logsResult.audit || [];
    qaLogs.value = logsResult.qa || [];
    eventSummary.value = logsResult.event_summary || [];
    targetSummary.value = logsResult.target_summary || [];
  } finally {
    loading.value = false;
  }
}

function resetFilters() {
  filters.value = {
    event_type: '',
    limit: 100,
    need_human: '',
    q: '',
    target_type: '',
  };
  void load();
}

function percent(value: number) {
  return Math.round((value || 0) * 100);
}

onMounted(load);
</script>

<template>
  <div class="audit-page p-5">
    <div class="audit-hero mb-5">
      <div>
        <div class="eyebrow">Audit & Metrics</div>
        <h1>统计审计中心</h1>
        <p>统一查看问答日志、账号、知识和在线记录的操作审计，支撑安全追踪与运行质量评估。</p>
      </div>
      <a-button :loading="loading" type="primary" @click="load">刷新数据</a-button>
    </div>

    <div class="grid gap-4 md:grid-cols-4">
      <a-card v-for="item in metricCards" :key="item.label" class="metric-card">
        <div class="text-sm text-slate-500">{{ item.label }}</div>
        <div :class="['metric-value', `tone-${item.tone}`]">{{ item.value }}</div>
      </a-card>
    </div>

    <div class="mt-5 grid gap-4 lg:grid-cols-3">
      <a-card class="lg:col-span-2" title="闭环指标">
        <div class="grid gap-4 md:grid-cols-3">
          <div class="ratio-box">
            <span>自助解决率</span>
            <strong>{{ percent(stats.self_solved_rate) }}%</strong>
            <a-progress :percent="percent(stats.self_solved_rate)" />
          </div>
          <div class="ratio-box">
            <span>人工协同率</span>
            <strong>{{ percent(stats.human_transfer_rate) }}%</strong>
            <a-progress :percent="percent(stats.human_transfer_rate)" status="active" />
          </div>
          <div class="ratio-box">
            <span>记录关闭数</span>
            <strong>{{ stats.closed_issues || 0 }}</strong>
            <small>已处理待回访：{{ stats.handled_issues || 0 }}</small>
          </div>
        </div>
      </a-card>
      <a-card title="审计对象分布">
        <a-empty v-if="!targetSummary.length" description="暂无审计数据" />
        <div v-for="item in targetSummary" v-else :key="item.target_type" class="summary-row">
          <span>{{ item.target_type }}</span>
          <a-tag>{{ item.count }}</a-tag>
        </div>
      </a-card>
    </div>

    <a-card class="mt-5" title="日志筛选">
      <div class="grid gap-3 md:grid-cols-5">
        <a-input v-model:value="filters.q" allow-clear placeholder="搜索内容、问题、答案" @press-enter="load" />
        <a-select v-model:value="filters.event_type" allow-clear placeholder="事件类型" @change="load">
          <a-select-option v-for="item in eventOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </a-select-option>
        </a-select>
        <a-select v-model:value="filters.target_type" allow-clear placeholder="对象类型" @change="load">
          <a-select-option v-for="item in targetOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </a-select-option>
        </a-select>
        <a-select v-model:value="filters.need_human" allow-clear placeholder="人工协同建议" @change="load">
          <a-select-option value="1">建议人工协同</a-select-option>
          <a-select-option value="0">可自助处理</a-select-option>
        </a-select>
        <div class="flex gap-2">
          <a-button :loading="loading" type="primary" @click="load">查询</a-button>
          <a-button @click="resetFilters">重置</a-button>
        </div>
      </div>
    </a-card>

    <a-card class="mt-5" title="操作审计日志">
      <a-table :columns="auditColumns" :data-source="auditLogs" :loading="loading" row-key="id">
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'event_type'">
            <a-tag color="blue">{{ record.event_type }}</a-tag>
          </template>
          <template v-if="column.dataIndex === 'target_type'">
            <a-tag>{{ record.target_type }}</a-tag>
          </template>
          <template v-if="column.dataIndex === 'content'">
            <span class="whitespace-pre-wrap">{{ record.content }}</span>
          </template>
        </template>
      </a-table>
    </a-card>

    <a-card class="mt-5" title="数字员工问答日志">
      <a-table :columns="qaColumns" :data-source="qaLogs" :loading="loading" row-key="id">
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'question'">
            <span class="whitespace-pre-wrap">{{ record.question }}</span>
          </template>
          <template v-if="column.dataIndex === 'answer'">
            <span class="line-clamp-3 whitespace-pre-wrap">{{ record.answer }}</span>
          </template>
          <template v-if="column.dataIndex === 'need_human'">
            <a-tag :color="record.need_human ? 'red' : 'green'">
              {{ record.need_human ? '人工协同' : '自助' }}
            </a-tag>
          </template>
          <template v-if="column.dataIndex === 'model_status'">
            <a-tag color="geekblue">{{ record.model_status }}</a-tag>
          </template>
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<style scoped>
.audit-page :deep(.ant-card) {
  border-radius: 20px;
}

.audit-hero {
  align-items: center;
  background:
    radial-gradient(circle at 84% 12%, rgb(56 189 248 / 30%), transparent 30%),
    linear-gradient(135deg, #0f172a, #1e3a8a 55%, #0f766e);
  border-radius: 28px;
  color: #fff;
  display: flex;
  justify-content: space-between;
  padding: 24px;
}

.audit-hero h1 {
  font-size: 30px;
  font-weight: 800;
  margin: 8px 0;
}

.audit-hero p {
  color: rgb(255 255 255 / 72%);
  margin: 0;
}

.eyebrow {
  color: #bae6fd;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

.metric-value {
  font-size: 30px;
  font-weight: 800;
  margin-top: 8px;
}

.tone-blue {
  color: #2563eb;
}

.tone-orange {
  color: #ea580c;
}

.tone-red {
  color: #dc2626;
}

.tone-green {
  color: #16a34a;
}

.tone-cyan {
  color: #0891b2;
}

.tone-gray {
  color: #475569;
}

.ratio-box {
  background: #f8fafc;
  border-radius: 18px;
  padding: 16px;
}

.ratio-box span,
.ratio-box small {
  color: #64748b;
  display: block;
}

.ratio-box strong {
  display: block;
  font-size: 28px;
  margin: 8px 0;
}

.summary-row {
  align-items: center;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  justify-content: space-between;
  padding: 10px 0;
}
</style>
