<script lang="ts" setup>
import { ref } from 'vue';

import { createIssue, handleIssue, listIssues, visitIssue } from '#/api/ops';

const form = ref({ title: '', description: '', contact_phone: '', priority: 'medium' });
const rows = ref<any[]>([]);
const solution = ref<Record<number, string>>({});

async function load() {
  rows.value = await listIssues();
}

async function submit() {
  await createIssue(form.value);
  form.value = { title: '', description: '', contact_phone: '', priority: 'medium' };
  await load();
}

async function handle(id: number) {
  await handleIssue(id, solution.value[id] || '');
  await load();
}

async function visit(id: number, resolved: boolean) {
  await visitIssue(id, {
    resolved,
    satisfaction_score: resolved ? 5 : 2,
    visit_result: resolved ? '用户确认问题已解决' : '用户反馈仍未解决',
  });
  await load();
}

load();
</script>

<template>
  <div class="p-5">
    <a-card title="创建在线记录">
      <div class="grid gap-4 md:grid-cols-3">
        <a-input v-model:value="form.title" placeholder="问题标题" />
        <a-input v-model:value="form.contact_phone" placeholder="联系电话" />
        <a-select v-model:value="form.priority">
          <a-select-option value="low">低</a-select-option>
          <a-select-option value="medium">中</a-select-option>
          <a-select-option value="high">高</a-select-option>
        </a-select>
      </div>
      <a-textarea v-model:value="form.description" class="mt-4" placeholder="问题描述" />
      <a-button class="mt-4" type="primary" @click="submit">提交记录</a-button>
    </a-card>

    <a-card class="mt-5" title="问题处理列表">
      <a-list :data-source="rows">
        <template #renderItem="{ item }">
          <a-list-item>
            <a-list-item-meta :description="item.description" :title="`#${item.id} ${item.title}`" />
            <div class="w-full max-w-xl">
              <div class="mb-2 flex gap-2">
                <a-tag>{{ item.status }}</a-tag>
                <a-tag>{{ item.priority }}</a-tag>
              </div>
              <p v-if="item.solution">处理结果：{{ item.solution }}</p>
              <a-textarea v-model:value="solution[item.id]" placeholder="填写处理结果" />
              <div class="mt-2 flex gap-2">
                <a-button size="small" type="primary" @click="handle(item.id)">提交处理</a-button>
                <a-button size="small" @click="visit(item.id, true)">回访已解决</a-button>
                <a-button size="small" @click="visit(item.id, false)">回访未解决</a-button>
              </div>
            </div>
          </a-list-item>
        </template>
      </a-list>
    </a-card>
  </div>
</template>

