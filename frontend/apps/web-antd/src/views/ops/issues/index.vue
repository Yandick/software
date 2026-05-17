<script lang="ts" setup>
import { computed, onMounted, ref } from 'vue';

import { useUserStore } from '@vben/stores';

import { message, Modal } from 'ant-design-vue';

import { createIssue, handleIssue, listIssues, visitIssue } from '#/api/ops';

const userStore = useUserStore();
const form = ref({
  category: 'account',
  contact_phone: '',
  description: '',
  impact_scope: '',
  priority: 'medium',
  title: '',
});
const rows = ref<any[]>([]);
const solution = ref<Record<number, string>>({});
const statusFilter = ref('');
const loading = ref(false);
const submitting = ref(false);
const handling = ref<Record<number, boolean>>({});

const canHandle = computed(() => {
  const role = userStore.userInfo?.roles?.[0];
  return role === 'admin' || role === 'ops';
});

const statusOptions = [
  { label: '全部', value: '' },
  { label: '待处理', value: 'pending' },
  { label: '已处理', value: 'handled' },
  { label: '已关闭', value: 'closed' },
];

const categoryOptions = [
  { label: '账号权限', value: 'account' },
  { label: '网络/VPN', value: 'network' },
  { label: '业务系统', value: 'business' },
  { label: '数据库/中间件', value: 'database' },
  { label: '其他', value: 'general' },
];

const priorityMeta: Record<string, { color: string; label: string }> = {
  high: { color: 'red', label: '高' },
  low: { color: 'green', label: '低' },
  medium: { color: 'orange', label: '中' },
};

const statusMeta: Record<string, { color: string; label: string }> = {
  closed: { color: 'green', label: '已关闭' },
  handled: { color: 'blue', label: '已处理/待回访' },
  pending: { color: 'orange', label: '待处理' },
};

async function load() {
  loading.value = true;
  try {
    rows.value = await listIssues(statusFilter.value);
  } finally {
    loading.value = false;
  }
}

async function submit() {
  if (!form.value.title.trim() || !form.value.description.trim()) {
    message.warning('请填写问题标题和问题描述');
    return;
  }
  submitting.value = true;
  try {
    await createIssue(form.value);
    form.value = {
      category: 'account',
      contact_phone: '',
      description: '',
      impact_scope: '',
      priority: 'medium',
      title: '',
    };
    message.success('在线记录已提交，运维人员会在处理台跟进');
    await load();
  } finally {
    submitting.value = false;
  }
}

async function handle(id: number) {
  const text = solution.value[id]?.trim();
  if (!text) {
    message.warning('请先填写处理结果');
    return;
  }
  handling.value[id] = true;
  try {
    await handleIssue(id, text);
    solution.value[id] = '';
    message.success('处理结果已提交，等待回访确认');
    await load();
  } finally {
    handling.value[id] = false;
  }
}

function confirmVisit(id: number, resolved: boolean) {
  Modal.confirm({
    content: resolved
      ? '确认用户已回访并认可解决结果？确认后会关闭记录，并把处理结果沉淀为知识案例。'
      : '确认用户反馈仍未解决？确认后记录会回到待处理状态。',
    okText: '确认',
    onOk: () => visit(id, resolved),
    title: resolved ? '回访已解决' : '回访未解决',
  });
}

async function visit(id: number, resolved: boolean) {
  await visitIssue(id, {
    resolved,
    satisfaction_score: resolved ? 5 : 2,
    visit_result: resolved ? '用户确认问题已解决' : '用户反馈仍未解决，需要继续处理',
  });
  message.success(resolved ? '记录已关闭并沉淀知识案例' : '记录已回到待处理状态');
  await load();
}

function metaOf(map: Record<string, { color: string; label: string }>, value: string) {
  return map[value] || { color: 'default', label: value || '未设置' };
}

onMounted(load);
</script>

<template>
  <div class="issue-page p-5">
    <div class="mb-5 rounded-3xl bg-slate-950 p-6 text-white">
      <div class="text-sm font-semibold uppercase tracking-[0.2em] text-cyan-200">Incident Desk</div>
      <h1 class="mt-3 text-3xl font-semibold">在线记录与人工处理台</h1>
      <p class="mt-3 max-w-3xl text-white/70">
        用户提交无法自助解决的问题；运维人员处理、回访，确认解决后自动沉淀为知识案例。
      </p>
    </div>

    <a-card title="创建在线记录">
      <div class="grid gap-4 md:grid-cols-3">
        <a-input v-model:value="form.title" placeholder="问题标题，如：VPN 无法连接" />
        <a-input v-model:value="form.contact_phone" placeholder="联系电话" />
        <a-select v-model:value="form.priority">
          <a-select-option value="low">低优先级</a-select-option>
          <a-select-option value="medium">中优先级</a-select-option>
          <a-select-option value="high">高优先级</a-select-option>
        </a-select>
        <a-select v-model:value="form.category" placeholder="问题分类">
          <a-select-option v-for="item in categoryOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </a-select-option>
        </a-select>
        <a-input v-model:value="form.impact_scope" class="md:col-span-2" placeholder="影响范围，如：单人/部门/全公司，是否影响生产" />
      </div>
      <a-textarea
        v-model:value="form.description"
        class="mt-4"
        :rows="4"
        placeholder="请描述故障现象、系统名称、报错信息、出现时间、已尝试步骤。"
      />
      <a-button class="mt-4" :loading="submitting" type="primary" @click="submit">提交记录</a-button>
    </a-card>

    <a-card class="mt-5" title="问题处理列表">
      <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
        <a-radio-group v-model:value="statusFilter" button-style="solid" @change="load">
          <a-radio-button v-for="item in statusOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </a-radio-button>
        </a-radio-group>
        <a-button :loading="loading" @click="load">刷新</a-button>
      </div>

      <a-empty v-if="!loading && rows.length === 0" description="暂无在线记录" />
      <a-list v-else :data-source="rows" :loading="loading" item-layout="vertical">
        <template #renderItem="{ item }">
          <a-list-item class="issue-item">
            <div class="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <section class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-2">
                  <strong class="text-lg">#{{ item.id }} {{ item.title }}</strong>
                  <a-tag :color="metaOf(statusMeta, item.status).color">
                    {{ metaOf(statusMeta, item.status).label }}
                  </a-tag>
                  <a-tag :color="metaOf(priorityMeta, item.priority).color">
                    优先级 {{ metaOf(priorityMeta, item.priority).label }}
                  </a-tag>
                  <a-tag>{{ item.category || 'general' }}</a-tag>
                </div>
                <p class="mt-3 whitespace-pre-wrap leading-7 text-slate-700">{{ item.description }}</p>
                <div class="mt-3 grid gap-2 text-sm text-slate-500 md:grid-cols-2">
                  <span>提交人：{{ item.requester_name || item.created_by_name || '未知' }}</span>
                  <span>联系电话：{{ item.contact_phone || '未填写' }}</span>
                  <span>影响范围：{{ item.impact_scope || '未填写' }}</span>
                  <span>更新时间：{{ item.updated_at }}</span>
                </div>
                <a-alert v-if="item.solution" class="mt-4" type="success" show-icon :message="`处理结果：${item.solution}`" />
              </section>

              <aside class="w-full rounded-2xl bg-slate-50 p-4 lg:w-[420px]">
                <div class="mb-3 font-medium">处理进度</div>
                <a-timeline v-if="item.events?.length">
                  <a-timeline-item v-for="event in item.events" :key="`${item.id}-${event.created_at}-${event.event_type}`">
                    <div class="text-sm font-medium">{{ event.content }}</div>
                    <div class="text-xs text-slate-500">
                      {{ event.operator_name || '系统' }} · {{ event.created_at }}
                    </div>
                  </a-timeline-item>
                </a-timeline>
                <a-empty v-else description="暂无处理事件" />

                <div v-if="canHandle && item.status !== 'closed'" class="mt-4 border-t border-slate-200 pt-4">
                  <a-textarea
                    v-model:value="solution[item.id]"
                    :rows="3"
                    placeholder="填写处理过程、根因、处理结果和注意事项"
                  />
                  <div class="mt-3 flex flex-wrap gap-2">
                    <a-button :loading="handling[item.id]" size="small" type="primary" @click="handle(item.id)">
                      提交处理
                    </a-button>
                    <a-button size="small" @click="confirmVisit(item.id, true)">回访已解决</a-button>
                    <a-button size="small" danger @click="confirmVisit(item.id, false)">回访未解决</a-button>
                  </div>
                </div>
                <a-alert
                  v-else-if="!canHandle"
                  class="mt-4"
                  message="普通用户可查看自己提交记录的处理状态，处理与回访由运维人员完成。"
                  type="info"
                  show-icon
                />
              </aside>
            </div>
          </a-list-item>
        </template>
      </a-list>
    </a-card>
  </div>
</template>

<style scoped>
.issue-page :deep(.ant-card) {
  border-radius: 20px;
}

.issue-item {
  border: 1px solid #e2e8f0;
  border-radius: 18px;
  margin-bottom: 14px;
  padding: 18px !important;
}
</style>
