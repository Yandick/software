<script lang="ts" setup>
import { computed, onMounted, ref } from 'vue';

import { useUserStore } from '@vben/stores';

import { message, Modal } from 'ant-design-vue';

import {
  createAccount,
  freezeAccount,
  listAccounts,
  unfreezeAccount,
  updateAccount,
} from '#/api/ops';

const userStore = useUserStore();
const form = ref({ account_name: '', permission_scope: 'basic_ops', remark: '' });
const editForm = ref({ id: 0, permission_scope: '', remark: '', status: 'active' });
const rows = ref<any[]>([]);
const q = ref('');
const loading = ref(false);
const submitting = ref(false);
const editOpen = ref(false);

const isAdmin = computed(() => userStore.userInfo?.roles?.[0] === 'admin');

const columns = [
  { title: '账号名', dataIndex: 'account_name' },
  { title: '权限范围', dataIndex: 'permission_scope' },
  { title: '状态', dataIndex: 'status' },
  { title: '备注', dataIndex: 'remark' },
  { title: '更新时间', dataIndex: 'updated_at' },
  { title: '操作', key: 'action', width: 240 },
];

async function load() {
  loading.value = true;
  try {
    rows.value = await listAccounts(q.value.trim());
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
    form.value = { account_name: '', permission_scope: 'basic_ops', remark: '' };
    message.success('运维账号已创建');
    await load();
  } finally {
    submitting.value = false;
  }
}

function openEdit(record: any) {
  editForm.value = {
    id: record.id,
    permission_scope: record.permission_scope,
    remark: record.remark,
    status: record.status,
  };
  editOpen.value = true;
}

async function saveEdit() {
  await updateAccount(editForm.value.id, {
    permission_scope: editForm.value.permission_scope,
    remark: editForm.value.remark,
    status: editForm.value.status,
  });
  editOpen.value = false;
  message.success('账号信息已更新');
  await load();
}

function confirmFreeze(record: any) {
  Modal.confirm({
    content: `冻结后账号 ${record.account_name} 将不能继续作为运维账号使用，操作会写入审计日志。`,
    okText: '确认冻结',
    okType: 'danger',
    onOk: async () => {
      await freezeAccount(record.id);
      message.success('账号已冻结');
      await load();
    },
    title: '确认冻结运维账号？',
  });
}

function confirmUnfreeze(record: any) {
  Modal.confirm({
    content: `解冻后账号 ${record.account_name} 将恢复启用，操作会写入审计日志。`,
    okText: '确认解冻',
    onOk: async () => {
      await unfreezeAccount(record.id);
      message.success('账号已解冻');
      await load();
    },
    title: '确认解冻运维账号？',
  });
}

function statusColor(status: string) {
  return status === 'active' ? 'green' : 'red';
}

function statusText(status: string) {
  return status === 'active' ? '启用' : '冻结';
}

onMounted(load);
</script>

<template>
  <div class="account-page p-5">
    <div class="mb-5 rounded-3xl bg-slate-950 p-6 text-white">
      <div class="text-sm font-semibold uppercase tracking-[0.2em] text-emerald-200">Account Console</div>
      <h1 class="mt-3 text-3xl font-semibold">运维账号管理</h1>
      <p class="mt-3 max-w-3xl text-white/70">
        账号新增、冻结、解冻、修改和查询必须由管理员执行；普通运维和审计角色只读查看。
      </p>
    </div>

    <a-alert
      v-if="!isAdmin"
      class="mb-5"
      message="当前账号没有账号变更权限"
      description="你可以查看账号信息；新增、修改、冻结、解冻需要管理员角色，并会写入审计日志。"
      show-icon
      type="info"
    />

    <a-card title="新增运维账号">
      <div class="grid gap-4 md:grid-cols-3">
        <a-input v-model:value="form.account_name" :disabled="!isAdmin" placeholder="账号名，如 ops_zhangsan" />
        <a-input v-model:value="form.permission_scope" :disabled="!isAdmin" placeholder="权限范围，如 basic_ops / db_readonly" />
        <a-input v-model:value="form.remark" :disabled="!isAdmin" placeholder="备注：人员、系统、有效期等" />
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
          placeholder="搜索账号名、权限范围或备注"
          @search="load"
        />
        <a-button :loading="loading" @click="load">刷新</a-button>
      </div>

      <a-table :columns="columns" :data-source="rows" :loading="loading" row-key="id">
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'status'">
            <a-tag :color="statusColor(record.status)">{{ statusText(record.status) }}</a-tag>
          </template>
          <template v-if="column.key === 'action'">
            <a-space>
              <a-button :disabled="!isAdmin" size="small" @click="openEdit(record)">修改</a-button>
              <a-button
                v-if="record.status === 'active'"
                :disabled="!isAdmin"
                danger
                size="small"
                @click="confirmFreeze(record)"
              >
                冻结
              </a-button>
              <a-button v-else :disabled="!isAdmin" size="small" @click="confirmUnfreeze(record)">
                解冻
              </a-button>
            </a-space>
          </template>
        </template>
      </a-table>
    </a-card>

    <a-modal v-model:open="editOpen" title="修改运维账号" ok-text="保存" @ok="saveEdit">
      <a-form layout="vertical">
        <a-form-item label="权限范围">
          <a-input v-model:value="editForm.permission_scope" placeholder="权限范围" />
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
  border-radius: 20px;
}
</style>
