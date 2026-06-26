<script lang="ts" setup>
import { computed, onMounted, ref } from 'vue';

import { useUserStore } from '@vben/stores';

import { message, Modal } from 'ant-design-vue';

import {
  createAccountApproval,
  createAccount,
  decideAccountApproval,
  exportAccounts,
  listAccountApprovals,
  listAccounts,
} from '#/api/ops';
import { useAutoRefresh } from '#/composables/use-auto-refresh';

const userStore = useUserStore();
const defaultAccountForm = () => ({
  account_name: '',
  contact_phone: '',
  department: '',
  expires_at: '',
  owner_name: '',
  permission_scope: 'basic_ops',
  remark: '',
  risk_level: 'medium',
});
const form = ref(defaultAccountForm());
const editForm = ref({ ...defaultAccountForm(), id: 0, status: 'active' });
const rows = ref<any[]>([]);
const q = ref('');
const loading = ref(false);
const submitting = ref(false);
const editOpen = ref(false);
const approvals = ref<any[]>([]);
const approvalsLoading = ref(false);
const approvalStatus = ref('pending');
const exportLoading = ref(false);

const currentRole = computed(() => userStore.userInfo?.roles?.[0] || '');
const isAdmin = computed(() => currentRole.value === 'admin');
const canRequestApproval = computed(() => ['admin', 'ops'].includes(currentRole.value));
const canExport = computed(() => ['admin', 'auditor'].includes(currentRole.value));
const expiringAccounts = computed(() =>
  rows.value.filter((item) => ['expired', 'expiring', 'invalid'].includes(item.expiry_status)),
);
const heroMetrics = computed(() => [
  { label: '账号总数', value: rows.value.length },
  { label: '启用账号', value: rows.value.filter((item) => item.status === 'active').length },
  { label: '待审批', value: approvals.value.filter((item) => item.status === 'pending').length },
]);
useAutoRefresh(load, 20000);

const columns = [
  { title: '账号名', dataIndex: 'account_name' },
  { title: '负责人', dataIndex: 'owner_name' },
  { title: '部门', dataIndex: 'department' },
  { title: '权限范围', dataIndex: 'permission_scope' },
  { title: '风险', dataIndex: 'risk_level' },
  { title: '状态', dataIndex: 'status' },
  { title: '有效期', dataIndex: 'expires_at', width: 160 },
  { title: '备注', dataIndex: 'remark' },
  { title: '更新时间', dataIndex: 'updated_at' },
  { title: '操作', key: 'action', width: 240 },
];

async function load() {
  loading.value = true;
  try {
    const [accountRows, approvalRows] = await Promise.all([
      listAccounts(q.value.trim()),
      listAccountApprovals(approvalStatus.value),
    ]);
    rows.value = accountRows;
    approvals.value = approvalRows;
  } finally {
    loading.value = false;
  }
}

async function submit() {
  if (!isAdmin.value) {
    message.warning('只有管理员可以新增运维账号');
    return;
  }
  if (!form.value.account_name.trim()) {
    message.warning('请填写账号名');
    return;
  }
  submitting.value = true;
  try {
    await createAccount(form.value);
    form.value = defaultAccountForm();
    message.success('运维账号已创建');
    await load();
  } finally {
    submitting.value = false;
  }
}

function openEdit(record: any) {
  if (!canRequestApproval.value) {
    message.warning('当前角色只能查看账号信息');
    return;
  }
  editForm.value = {
    account_name: record.account_name,
    contact_phone: record.contact_phone,
    department: record.department,
    expires_at: record.expires_at,
    id: record.id,
    owner_name: record.owner_name,
    permission_scope: record.permission_scope,
    remark: record.remark,
    risk_level: record.risk_level || 'medium',
    status: record.status,
  };
  editOpen.value = true;
}

async function saveEdit() {
  if (!canRequestApproval.value) {
    message.warning('当前角色只能查看账号信息');
    return;
  }
  await createAccountApproval({
    account_id: editForm.value.id,
    action: 'update',
    payload: {
      contact_phone: editForm.value.contact_phone,
      department: editForm.value.department,
      expires_at: editForm.value.expires_at,
      owner_name: editForm.value.owner_name,
      permission_scope: editForm.value.permission_scope,
      remark: editForm.value.remark,
      risk_level: editForm.value.risk_level,
      status: editForm.value.status,
    },
    reason: '申请修改运维账号信息',
  });
  editOpen.value = false;
  message.success('账号修改已提交审批，审批通过后生效');
  await load();
}

function confirmFreeze(record: any) {
  if (!canRequestApproval.value) {
    message.warning('当前角色只能查看账号信息');
    return;
  }
  Modal.confirm({
    content: `冻结 ${record.account_name} 属于高风险操作，将先创建审批单，审批通过后才会生效。`,
    okText: '提交审批',
    okType: 'danger',
    onOk: async () => {
      await createAccountApproval({
        account_id: record.id,
        action: 'freeze',
        payload: {},
        reason: '申请冻结运维账号',
      });
      message.success('冻结申请已提交审批');
      await load();
    },
    title: '提交冻结审批？',
  });
}

function confirmUnfreeze(record: any) {
  if (!canRequestApproval.value) {
    message.warning('当前角色只能查看账号信息');
    return;
  }
  Modal.confirm({
    content: `解冻 ${record.account_name} 将先创建审批单，审批通过后才会恢复启用。`,
    okText: '提交审批',
    onOk: async () => {
      await createAccountApproval({
        account_id: record.id,
        action: 'unfreeze',
        payload: {},
        reason: '申请解冻运维账号',
      });
      message.success('解冻申请已提交审批');
      await load();
    },
    title: '提交解冻审批？',
  });
}

async function decideApproval(id: number, decision: string) {
  approvalsLoading.value = true;
  try {
    await decideAccountApproval(id, decision, decision === 'approved' ? '审批通过' : '审批拒绝');
    message.success(decision === 'approved' ? '审批已通过并执行操作' : '审批已拒绝');
    await load();
  } finally {
    approvalsLoading.value = false;
  }
}

function isOwnApproval(record: any) {
  return Number(record.requested_by) === Number(userStore.userInfo?.userId);
}

async function downloadAccounts() {
  if (!canExport.value) {
    message.warning('只有管理员或审计员可以导出账号清单');
    return;
  }
  exportLoading.value = true;
  try {
    const result = await exportAccounts(q.value.trim());
    const blob = new Blob([result.content || ''], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = result.filename || 'ops_accounts.csv';
    link.click();
    URL.revokeObjectURL(url);
    message.success(`已导出 ${result.count || 0} 条账号记录`);
  } finally {
    exportLoading.value = false;
  }
}

function statusColor(status: string) {
  return status === 'active' ? 'green' : 'red';
}

function statusText(status: string) {
  return status === 'active' ? '启用' : '冻结';
}

function riskColor(risk: string) {
  const colors: Record<string, string> = { high: 'red', low: 'green', medium: 'orange' };
  return colors[risk] || 'default';
}

function riskText(risk: string) {
  const labels: Record<string, string> = { high: '高风险', low: '低风险', medium: '中风险' };
  return labels[risk] || risk || '未设置';
}

function actionText(action: string) {
  const labels: Record<string, string> = { freeze: '冻结', unfreeze: '解冻', update: '修改' };
  return labels[action] || action;
}

function approvalStatusText(status: string) {
  const labels: Record<string, string> = { approved: '已通过', pending: '待审批', rejected: '已拒绝' };
  return labels[status] || status;
}

function approvalStatusColor(status: string) {
  const colors: Record<string, string> = { approved: 'green', pending: 'orange', rejected: 'red' };
  return colors[status] || 'default';
}

function expiryColor(status: string) {
  const colors: Record<string, string> = {
    expired: 'red',
    expiring: 'orange',
    invalid: 'purple',
    none: 'default',
    valid: 'green',
  };
  return colors[status] || 'default';
}

function expiryText(record: any) {
  if (!record.expires_at) return '未设置';
  if (record.expiry_status === 'expired') return `已过期 ${Math.abs(record.days_to_expire)} 天`;
  if (record.expiry_status === 'expiring') return `${record.days_to_expire} 天后到期`;
  if (record.expiry_status === 'invalid') return '日期异常';
  return record.expires_at;
}

function payloadSummary(payloadJson = '{}') {
  try {
    const payload = JSON.parse(payloadJson || '{}');
    const labels: Record<string, string> = {
      contact_phone: '联系方式',
      department: '部门',
      expires_at: '有效期',
      owner_name: '负责人',
      permission_scope: '权限范围',
      remark: '备注',
      risk_level: '风险等级',
      status: '状态',
    };
    return Object.entries(payload)
      .map(([key, value]) => `${labels[key] || key}: ${value || '空'}`)
      .join('；');
  } catch {
    return payloadJson;
  }
}

onMounted(load);
</script>

<template>
  <div class="account-page p-5">
    <div class="ops-hero mb-5">
      <div class="ops-kicker">Account Console</div>
      <h1>运维账号管理</h1>
      <p>
        账号新增由管理员执行；冻结、解冻和修改属于高风险操作，必须先提交审批，审批通过后才会真正生效。
      </p>
      <div class="ops-hero-metrics">
        <span v-for="item in heroMetrics" :key="item.label">
          <b>{{ item.value }}</b>
          <small>{{ item.label }}</small>
        </span>
      </div>
    </div>

    <a-alert
      v-if="!isAdmin"
      class="mb-5"
      message="当前账号没有账号变更权限"
      description="当前角色可查看账号信息；新增需管理员权限，修改、冻结、解冻需提交审批并写入审计日志。"
      show-icon
      type="info"
    />

    <a-alert
      v-if="expiringAccounts.length"
      class="mb-5"
      :message="`发现 ${expiringAccounts.length} 个账号即将到期、已过期或有效期格式异常`"
      description="请在账号列表中查看有效期标签，必要时提交修改审批或冻结审批，避免过期权限继续使用。"
      show-icon
      type="warning"
    />

    <a-card title="新增运维账号">
      <div class="grid gap-4 md:grid-cols-3">
        <a-input v-model:value="form.account_name" :disabled="!isAdmin" placeholder="账号名，如 ops_zhangsan" />
        <a-input v-model:value="form.owner_name" :disabled="!isAdmin" placeholder="负责人姓名" />
        <a-input v-model:value="form.department" :disabled="!isAdmin" placeholder="所属部门" />
        <a-input v-model:value="form.contact_phone" :disabled="!isAdmin" placeholder="联系方式" />
        <a-input v-model:value="form.permission_scope" :disabled="!isAdmin" placeholder="权限范围，如 basic_ops / db_readonly" />
        <a-select v-model:value="form.risk_level" :disabled="!isAdmin" placeholder="风险等级">
          <a-select-option value="low">低风险</a-select-option>
          <a-select-option value="medium">中风险</a-select-option>
          <a-select-option value="high">高风险</a-select-option>
        </a-select>
        <a-input v-model:value="form.expires_at" :disabled="!isAdmin" placeholder="有效期，如 2026-12-31" />
        <a-input v-model:value="form.remark" :disabled="!isAdmin" placeholder="备注：系统、申请单、授权范围等" />
      </div>
      <a-button class="mt-4" :disabled="!isAdmin" :loading="submitting" type="primary" @click="submit">
        新增账号
      </a-button>
    </a-card>

    <a-card class="mt-5" title="账号列表">
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <a-input-search
          v-model:value="q"
          allow-clear
          class="max-w-lg"
          placeholder="搜索账号名、负责人、部门、权限、风险或备注"
          @search="load"
        />
        <a-button :disabled="!canExport" :loading="exportLoading" @click="downloadAccounts">
          导出 CSV
        </a-button>
        <a-tag color="blue">自动刷新中</a-tag>
      </div>

      <a-table :columns="columns" :data-source="rows" :loading="loading" row-key="id" :scroll="{ x: 1200 }">
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'risk_level'">
            <a-tag :color="riskColor(record.risk_level)">{{ riskText(record.risk_level) }}</a-tag>
          </template>
          <template v-if="column.dataIndex === 'status'">
            <a-tag :color="statusColor(record.status)">{{ statusText(record.status) }}</a-tag>
          </template>
          <template v-if="column.dataIndex === 'expires_at'">
            <a-tag :color="expiryColor(record.expiry_status)">{{ expiryText(record) }}</a-tag>
          </template>
          <template v-if="column.key === 'action'">
            <a-space>
              <a-button :disabled="!canRequestApproval" size="small" @click="openEdit(record)">修改</a-button>
              <a-button
                v-if="record.status === 'active'"
                :disabled="!canRequestApproval"
                danger
                size="small"
                @click="confirmFreeze(record)"
              >
                冻结
              </a-button>
              <a-button v-else :disabled="!canRequestApproval" size="small" @click="confirmUnfreeze(record)">
                解冻
              </a-button>
            </a-space>
          </template>
        </template>
      </a-table>
    </a-card>

    <a-card class="mt-5" title="账号操作审批">
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <a-select v-model:value="approvalStatus" class="w-40" @change="load">
          <a-select-option value="pending">待审批</a-select-option>
          <a-select-option value="approved">已通过</a-select-option>
          <a-select-option value="rejected">已拒绝</a-select-option>
          <a-select-option value="">全部</a-select-option>
        </a-select>
        <a-tag color="blue">自动刷新中</a-tag>
      </div>
      <a-table :data-source="approvals" :loading="approvalsLoading || loading" row-key="id" :columns="[
        { title: '账号', dataIndex: 'account_name' },
        { title: '动作', dataIndex: 'action' },
        { title: '状态', dataIndex: 'status' },
        { title: '申请人', dataIndex: 'requester_name' },
        { title: '原因', dataIndex: 'reason' },
        { title: '变更摘要', dataIndex: 'payload_json', width: 280 },
        { title: '时间', dataIndex: 'created_at' },
        { title: '操作', key: 'actionButtons', width: 180 },
      ]">
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'action'">
            <a-tag color="orange">{{ actionText(record.action) }}</a-tag>
          </template>
          <template v-if="column.dataIndex === 'status'">
            <a-tag :color="approvalStatusColor(record.status)">{{ approvalStatusText(record.status) }}</a-tag>
          </template>
          <template v-if="column.dataIndex === 'payload_json'">
            <span class="text-slate-500">{{ payloadSummary(record.payload_json) || '无' }}</span>
          </template>
          <template v-if="column.key === 'actionButtons'">
            <a-space v-if="record.status === 'pending'">
              <a-button :disabled="!isAdmin || isOwnApproval(record)" size="small" type="primary" @click="decideApproval(record.id, 'approved')">
                通过
              </a-button>
              <a-button :disabled="!isAdmin || isOwnApproval(record)" danger size="small" @click="decideApproval(record.id, 'rejected')">
                拒绝
              </a-button>
            </a-space>
            <span v-else class="text-slate-400">已处理</span>
          </template>
        </template>
      </a-table>
    </a-card>

    <a-modal v-model:open="editOpen" title="修改运维账号" ok-text="提交审批" :width="720" @ok="saveEdit">
      <a-form layout="vertical">
        <div class="grid gap-3 md:grid-cols-2">
          <a-form-item label="负责人">
            <a-input v-model:value="editForm.owner_name" placeholder="负责人姓名" />
          </a-form-item>
          <a-form-item label="所属部门">
            <a-input v-model:value="editForm.department" placeholder="所属部门" />
          </a-form-item>
          <a-form-item label="联系方式">
            <a-input v-model:value="editForm.contact_phone" placeholder="联系方式" />
          </a-form-item>
          <a-form-item label="有效期">
            <a-input v-model:value="editForm.expires_at" placeholder="如 2026-12-31" />
          </a-form-item>
        </div>
        <a-form-item label="权限范围">
          <a-input v-model:value="editForm.permission_scope" placeholder="权限范围" />
        </a-form-item>
        <a-form-item label="风险等级">
          <a-select v-model:value="editForm.risk_level">
            <a-select-option value="low">低风险</a-select-option>
            <a-select-option value="medium">中风险</a-select-option>
            <a-select-option value="high">高风险</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="状态">
          <a-select v-model:value="editForm.status">
            <a-select-option value="active">启用</a-select-option>
            <a-select-option value="frozen">冻结</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="备注">
          <a-textarea v-model:value="editForm.remark" :rows="4" placeholder="备注" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.account-page :deep(.ant-card) {
  border-radius: 8px;
}
</style>
