<script lang="ts" setup>
import { computed, onMounted, ref } from 'vue';

import { useUserStore } from '@vben/stores';

import { message, Modal } from 'ant-design-vue';

import {
  changeKnowledgeStatus,
  checkKnowledgeSensitive,
  createKnowledge,
  listKnowledge,
  updateKnowledge,
  uploadKnowledgeDocument,
} from '#/api/ops';

const userStore = useUserStore();
const emptyForm = {
  content: '',
  source_type: 'faq',
  status: 'pending_review',
  tags: '',
  title: '',
};

const form = ref({ ...emptyForm });
const editForm = ref({ id: 0, ...emptyForm });
const rows = ref<any[]>([]);
const q = ref('');
const statusFilter = ref('');
const sourceTypeFilter = ref('');
const loading = ref(false);
const submitting = ref(false);
const sensitiveChecking = ref(false);
const editOpen = ref(false);
const documentFileList = ref<any[]>([]);
const documentTags = ref('');
const documentTitle = ref('');
const documentUploading = ref(false);

const canMaintain = computed(() => {
  const role = userStore.userInfo?.roles?.[0];
  return role === 'admin' || role === 'ops';
});

const canPublish = computed(() => userStore.userInfo?.roles?.[0] === 'admin');
const heroMetrics = computed(() => [
  { label: '知识条目', value: rows.value.length },
  { label: '待审核', value: rows.value.filter((item) => item.status === 'pending_review').length },
  { label: '已发布', value: rows.value.filter((item) => item.status === 'published').length },
]);

const columns = [
  { dataIndex: 'title', title: '标题', width: 240 },
  { dataIndex: 'version', title: '版本', width: 90 },
  { dataIndex: 'source_type', title: '来源', width: 120 },
  { dataIndex: 'status', title: '状态', width: 120 },
  { dataIndex: 'tags', title: '标签', width: 180 },
  { dataIndex: 'updated_at', title: '更新时间', width: 180 },
  { key: 'action', title: '操作', width: 280 },
];

const sourceTypeOptions = [
  { label: 'FAQ 问答', value: 'faq' },
  { label: 'Runbook', value: 'runbook' },
  { label: '处理案例', value: 'case' },
  { label: '制度流程', value: 'policy' },
  { label: '上传文档', value: 'document' },
  { label: '其他', value: 'other' },
];

const statusOptions = [
  { color: 'orange', label: '待审核', value: 'pending_review' },
  { color: 'green', label: '已发布', value: 'published' },
  { color: 'default', label: '已下线', value: 'offline' },
];

async function load() {
  loading.value = true;
  try {
    rows.value = await listKnowledge({
      q: q.value.trim(),
      source_type: sourceTypeFilter.value,
      status: statusFilter.value,
    });
  } finally {
    loading.value = false;
  }
}

async function submit() {
  if (!canMaintain.value) {
    message.warning('只有管理员或运维人员可以维护知识库');
    return;
  }
  if (!form.value.title.trim() || !form.value.content.trim()) {
    message.warning('请填写知识标题和内容');
    return;
  }
  submitting.value = true;
  try {
    const check = await runSensitiveCheck('create', false);
    if (form.value.status === 'published' && check?.blocking) {
      message.error('知识内容包含高风险敏感信息，请先脱敏后再发布');
      return;
    }
    await createKnowledge(form.value);
    form.value = { ...emptyForm };
    message.success('知识已提交，可审核发布后进入数字员工检索范围');
    await load();
  } finally {
    submitting.value = false;
  }
}

function beforeDocumentUpload(file: any) {
  documentFileList.value = [file];
  if (!documentTitle.value.trim()) {
    documentTitle.value = String(file.name || '').replace(/\.[^.]+$/, '');
  }
  return false;
}

function removeDocumentUpload() {
  documentFileList.value = [];
}

async function submitDocumentUpload() {
  if (!canMaintain.value) {
    message.warning('只有管理员或运维人员可以导入知识文档');
    return;
  }
  const file = documentFileList.value[0]?.originFileObj || documentFileList.value[0];
  if (!file) {
    message.warning('请选择要导入的纯文本知识文档');
    return;
  }
  documentUploading.value = true;
  try {
    const result = await uploadKnowledgeDocument(file, {
      tags: documentTags.value.trim(),
      title: documentTitle.value.trim(),
    });
    message.success(`已生成 ${result.chunk_count} 个待审核知识片段，脱敏 ${result.redacted_count} 个片段`);
    documentFileList.value = [];
    documentTags.value = '';
    documentTitle.value = '';
    sourceTypeFilter.value = 'document';
    statusFilter.value = 'pending_review';
    await load();
  } finally {
    documentUploading.value = false;
  }
}

function openEdit(record: any) {
  if (!canEditRecord(record)) {
    message.warning('运维人员只能编辑待审核知识候选');
    return;
  }
  editForm.value = {
    content: record.content,
    id: record.id,
    source_type: record.source_type,
    status: record.status,
    tags: record.tags,
    title: record.title,
  };
  editOpen.value = true;
}

async function saveEdit() {
  if (!editForm.value.title.trim() || !editForm.value.content.trim()) {
    message.warning('请填写知识标题和内容');
    return;
  }
  const check = await runSensitiveCheck('edit', false);
  if (editForm.value.status === 'published' && check?.blocking) {
    message.error('知识内容包含高风险敏感信息，请先脱敏后再发布');
    return;
  }
  await updateKnowledge(editForm.value.id, {
    content: editForm.value.content,
    source_type: editForm.value.source_type,
    status: editForm.value.status,
    tags: editForm.value.tags,
    title: editForm.value.title,
  });
  editOpen.value = false;
  message.success('知识条目已更新');
  await load();
}

function canEditRecord(record: any) {
  return canPublish.value || (canMaintain.value && record.status === 'pending_review');
}

function confirmStatus(record: any, status: string) {
  const meta = statusMeta(status);
  if (status === 'published' && record.sensitive_check?.blocking) {
    Modal.warning({
      content: sensitiveSummary(record.sensitive_check),
      okText: '知道了',
      title: '发布前需要先脱敏',
    });
    return;
  }
  Modal.confirm({
    content:
      status === 'published'
        ? '发布后该条知识会进入数字员工知识检索范围，请确认内容已审核且不含敏感信息。'
        : '状态变更会写入审计日志；下线后该条知识不会再被数字员工用于回答。',
    okText: `确认${meta.label}`,
    onOk: async () => {
      await changeKnowledgeStatus(record.id, status, `${meta.label}：${record.title}`);
      message.success(`知识状态已更新为${meta.label}`);
      await load();
    },
    title: `确认将「${record.title}」设为${meta.label}？`,
  });
}

function sensitiveSummary(check: any) {
  const findings = check?.findings || [];
  if (!findings.length) {
    return '未发现手机号、证件号、密码、密钥等敏感信息。';
  }
  return findings
    .map((item: any) => `${item.label} ${item.count || 0} 处，风险等级：${item.severity}`)
    .join('；');
}

function applyRedacted(target: 'create' | 'edit', check: any) {
  const redacted = check?.redacted || {};
  if (target === 'create') {
    form.value = {
      ...form.value,
      content: redacted.content ?? form.value.content,
      tags: redacted.tags ?? form.value.tags,
      title: redacted.title ?? form.value.title,
    };
  } else {
    editForm.value = {
      ...editForm.value,
      content: redacted.content ?? editForm.value.content,
      tags: redacted.tags ?? editForm.value.tags,
      title: redacted.title ?? editForm.value.title,
    };
  }
}

async function runSensitiveCheck(target: 'create' | 'edit' = 'create', interactive = true) {
  const current = target === 'create' ? form.value : editForm.value;
  if (!current.title.trim() && !current.content.trim() && !current.tags.trim()) {
    if (interactive) {
      message.warning('请先填写知识标题或内容');
    }
    return null;
  }
  sensitiveChecking.value = true;
  try {
    const check = await checkKnowledgeSensitive({
      content: current.content,
      tags: current.tags,
      title: current.title,
    });
    if (!check.has_sensitive) {
      if (interactive) {
        message.success('未发现需要脱敏的敏感信息');
      }
      return check;
    }
    if (interactive) {
      Modal.confirm({
        content: `${sensitiveSummary(check)}。是否使用脱敏后的文本替换当前表单？`,
        okText: '一键脱敏',
        onOk: () => applyRedacted(target, check),
        title: check.blocking ? '发现高风险敏感信息' : '发现疑似敏感信息',
      });
    }
    return check;
  } finally {
    sensitiveChecking.value = false;
  }
}

function sourceTypeLabel(value: string) {
  return sourceTypeOptions.find((item) => item.value === value)?.label || value || '未设置';
}

function statusMeta(value: string) {
  return statusOptions.find((item) => item.value === value) || { color: 'default', label: value || '未设置', value };
}

onMounted(load);
</script>

<template>
  <div class="knowledge-page p-5">
    <div class="mb-5 rounded-3xl bg-slate-950 p-6 text-white">
      <div class="text-sm font-semibold uppercase tracking-[0.2em] text-amber-200">Knowledge Base</div>
      <h1 class="mt-3 text-3xl font-semibold">知识库维护与审核</h1>
      <p class="mt-3 max-w-3xl text-white/70">
        常见问答、操作规程、制度流程和已解决案例先作为候选知识沉淀，审核发布后进入数字员工检索范围。
      </p>
      <div class="ops-hero-metrics">
        <span v-for="item in heroMetrics" :key="item.label">
          <b>{{ item.value }}</b>
          <small>{{ item.label }}</small>
        </span>
      </div>
    </div>

    <a-alert
      v-if="!canMaintain"
      class="mb-5"
      message="当前账号没有知识维护权限"
      description="当前角色可查看知识条目；新增、编辑候选、发布、下线需具备对应权限，并将写入审计日志。"
      show-icon
      type="info"
    />
    <a-alert
      v-else-if="!canPublish"
      class="mb-5"
      message="运维人员可提交和编辑待审核知识候选"
      description="已发布或已下线知识由管理员发布、下线或退回审核，避免未经审核的内容影响数字员工检索。"
      show-icon
      type="warning"
    />

    <a-card title="提交候选知识">
      <div class="grid gap-4 md:grid-cols-2">
        <a-input v-model:value="form.title" :disabled="!canMaintain" placeholder="知识标题，如：VPN 无法连接处理步骤" />
        <a-input v-model:value="form.tags" :disabled="!canMaintain" placeholder="标签，如：VPN,网络,远程办公" />
        <a-select v-model:value="form.source_type" :disabled="!canMaintain" placeholder="来源类型">
          <a-select-option v-for="item in sourceTypeOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </a-select-option>
        </a-select>
        <a-select v-model:value="form.status" :disabled="!canPublish" placeholder="审核状态">
          <a-select-option v-for="item in statusOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </a-select-option>
        </a-select>
      </div>
      <a-textarea
        v-model:value="form.content"
        class="mt-4"
        :disabled="!canMaintain"
        :rows="5"
        placeholder="请填写处理步骤、适用范围、风险提示和人工协同条件。"
      />
      <a-button class="mt-4" :disabled="!canMaintain" :loading="submitting" type="primary" @click="submit">
        提交知识
      </a-button>
      <a-button class="ml-2 mt-4" :disabled="!canMaintain" :loading="sensitiveChecking" @click="runSensitiveCheck('create')">
        敏感信息检查
      </a-button>
    </a-card>

    <a-card v-if="canMaintain" class="mt-5" title="导入知识文档">
      <div class="grid gap-4 md:grid-cols-2">
        <a-input v-model:value="documentTitle" placeholder="文档标题，默认使用文件名" />
        <a-input v-model:value="documentTags" placeholder="标签，如：VPN,证书,远程办公" />
      </div>
      <div class="mt-4 flex flex-wrap items-center gap-3">
        <a-upload
          v-model:file-list="documentFileList"
          accept=".txt,.md,.markdown,.log,.csv,text/plain,text/markdown,text/csv"
          :before-upload="beforeDocumentUpload"
          :max-count="1"
          @remove="removeDocumentUpload"
        >
          <a-button>选择文档</a-button>
        </a-upload>
        <a-button :disabled="!documentFileList.length" :loading="documentUploading" type="primary" @click="submitDocumentUpload">
          导入为候选知识
        </a-button>
      </div>
    </a-card>

    <a-card class="mt-5" title="知识条目">
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <a-input-search
          v-model:value="q"
          allow-clear
          class="min-w-[260px] max-w-lg"
          placeholder="搜索标题、内容或标签"
          @search="load"
        />
        <a-select v-model:value="statusFilter" allow-clear class="w-36" placeholder="状态" @change="load">
          <a-select-option v-for="item in statusOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </a-select-option>
        </a-select>
        <a-select v-model:value="sourceTypeFilter" allow-clear class="w-40" placeholder="来源" @change="load">
          <a-select-option v-for="item in sourceTypeOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </a-select-option>
        </a-select>
        <a-button :loading="loading" @click="load">刷新</a-button>
      </div>

      <a-table :columns="columns" :data-source="rows" :loading="loading" row-key="id">
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'title'">
            <div class="font-medium">#{{ record.id }} {{ record.title }}</div>
            <div class="mt-1 line-clamp-2 text-sm text-slate-500">{{ record.content }}</div>
            <a-tag v-if="record.sensitive_check?.blocking" class="mt-2" color="red">需脱敏后发布</a-tag>
            <a-tag v-else-if="record.sensitive_check?.has_sensitive" class="mt-2" color="orange">疑似敏感信息</a-tag>
          </template>
          <template v-if="column.dataIndex === 'source_type'">
            <a-tag>{{ sourceTypeLabel(record.source_type) }}</a-tag>
          </template>
          <template v-if="column.dataIndex === 'version'">
            <a-tag color="geekblue">v{{ record.version || 1 }}</a-tag>
          </template>
          <template v-if="column.dataIndex === 'status'">
            <a-tag :color="statusMeta(record.status).color">{{ statusMeta(record.status).label }}</a-tag>
            <div v-if="record.review_note" class="mt-1 text-xs text-slate-500">
              审核：{{ record.review_note }}
            </div>
          </template>
          <template v-if="column.dataIndex === 'tags'">
            <a-space wrap>
              <a-tag v-for="tag in String(record.tags || '').split(',').filter(Boolean)" :key="tag">
                {{ tag.trim() }}
              </a-tag>
              <span v-if="!record.tags" class="text-slate-400">未设置</span>
            </a-space>
          </template>
          <template v-if="column.key === 'action'">
            <a-space wrap>
              <a-button :disabled="!canEditRecord(record)" size="small" @click="openEdit(record)">编辑</a-button>
              <a-button
                v-if="record.status !== 'published'"
                :disabled="!canPublish"
                size="small"
                type="primary"
                @click="confirmStatus(record, 'published')"
              >
                发布
              </a-button>
              <a-button
                v-if="record.status !== 'pending_review'"
                :disabled="!canPublish"
                size="small"
                @click="confirmStatus(record, 'pending_review')"
              >
                退回审核
              </a-button>
              <a-button
                v-if="record.status !== 'offline'"
                :disabled="!canPublish"
                danger
                size="small"
                @click="confirmStatus(record, 'offline')"
              >
                下线
              </a-button>
            </a-space>
          </template>
        </template>
      </a-table>
    </a-card>

    <a-modal v-model:open="editOpen" title="编辑知识条目" ok-text="保存" @ok="saveEdit">
      <a-form layout="vertical">
        <a-form-item label="标题">
          <a-input v-model:value="editForm.title" placeholder="知识标题" />
        </a-form-item>
        <a-form-item label="标签">
          <a-input v-model:value="editForm.tags" placeholder="使用英文逗号分隔多个标签" />
        </a-form-item>
        <a-form-item label="来源类型">
          <a-select v-model:value="editForm.source_type">
            <a-select-option v-for="item in sourceTypeOptions" :key="item.value" :value="item.value">
              {{ item.label }}
            </a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="审核状态">
          <a-select v-model:value="editForm.status" :disabled="!canPublish">
            <a-select-option v-for="item in statusOptions" :key="item.value" :value="item.value">
              {{ item.label }}
            </a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="内容">
          <a-textarea v-model:value="editForm.content" :rows="7" placeholder="知识内容" />
        </a-form-item>
        <a-button :loading="sensitiveChecking" @click="runSensitiveCheck('edit')">敏感信息检查</a-button>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.knowledge-page :deep(.ant-card) {
  border-radius: 20px;
}
</style>
