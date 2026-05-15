<script lang="ts" setup>
import { ref } from 'vue';

import { createAccount, freezeAccount, listAccounts, unfreezeAccount } from '#/api/ops';

const form = ref({ account_name: '', permission_scope: 'basic_ops', remark: '' });
const rows = ref<any[]>([]);

async function load() {
  rows.value = await listAccounts();
}

async function submit() {
  await createAccount(form.value);
  form.value = { account_name: '', permission_scope: 'basic_ops', remark: '' };
  await load();
}

async function freeze(id: number) {
  await freezeAccount(id);
  await load();
}

async function unfreeze(id: number) {
  await unfreezeAccount(id);
  await load();
}

load();
</script>

<template>
  <div class="p-5">
    <a-card title="新增运维账号">
      <div class="grid gap-4 md:grid-cols-3">
        <a-input v-model:value="form.account_name" placeholder="账号名" />
        <a-input v-model:value="form.permission_scope" placeholder="权限范围" />
        <a-input v-model:value="form.remark" placeholder="备注" />
      </div>
      <a-button class="mt-4" type="primary" @click="submit">新增账号</a-button>
    </a-card>

    <a-card class="mt-5" title="账号列表">
      <a-table :columns="[
        { title: '账号名', dataIndex: 'account_name' },
        { title: '权限范围', dataIndex: 'permission_scope' },
        { title: '状态', dataIndex: 'status' },
        { title: '备注', dataIndex: 'remark' },
        { title: '操作', key: 'action' },
      ]" :data-source="rows" row-key="id">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'action'">
            <a-space>
              <a-button size="small" @click="freeze(record.id)">冻结</a-button>
              <a-button size="small" @click="unfreeze(record.id)">解冻</a-button>
            </a-space>
          </template>
        </template>
      </a-table>
    </a-card>
  </div>
</template>

