<script lang="ts" setup>
import { ref } from 'vue';

import { getAuditLogs, getStats } from '#/api/ops';

const stats = ref<any>({});
const logs = ref<any[]>([]);

async function load() {
  stats.value = await getStats();
  const result = await getAuditLogs();
  logs.value = result.audit || [];
}

load();
</script>

<template>
  <div class="p-5">
    <div class="grid gap-4 md:grid-cols-4">
      <a-card title="问答次数">{{ stats.total_qa || 0 }}</a-card>
      <a-card title="转人工率">{{ Math.round((stats.human_transfer_rate || 0) * 100) }}%</a-card>
      <a-card title="问题记录">{{ stats.issues || 0 }}</a-card>
      <a-card title="运维账号">{{ stats.accounts || 0 }}</a-card>
    </div>

    <a-card class="mt-5" title="审计日志">
      <a-table :columns="[
        { title: '事件', dataIndex: 'event_type' },
        { title: '对象', dataIndex: 'target_type' },
        { title: '内容', dataIndex: 'content' },
        { title: '时间', dataIndex: 'created_at' },
      ]" :data-source="logs" row-key="id" />
    </a-card>
  </div>
</template>

