<script lang="ts" setup>
import { ref } from 'vue';

import { createKnowledge, listKnowledge } from '#/api/ops';

const form = ref({ title: '', content: '', tags: '', source_type: 'faq', status: 'published' });
const rows = ref<any[]>([]);

async function load() {
  rows.value = await listKnowledge();
}

async function submit() {
  await createKnowledge(form.value);
  form.value = { title: '', content: '', tags: '', source_type: 'faq', status: 'published' };
  await load();
}

load();
</script>

<template>
  <div class="p-5">
    <a-card title="新增知识条目">
      <div class="grid gap-4 md:grid-cols-2">
        <a-input v-model:value="form.title" placeholder="知识标题" />
        <a-input v-model:value="form.tags" placeholder="标签，如：账号,冻结,FAQ" />
      </div>
      <a-textarea v-model:value="form.content" class="mt-4" :rows="5" placeholder="知识内容" />
      <a-button class="mt-4" type="primary" @click="submit">新增知识</a-button>
    </a-card>

    <div class="mt-5 grid gap-4 md:grid-cols-2">
      <a-card v-for="item in rows" :key="item.id" :title="item.title">
        <p>{{ item.content }}</p>
        <div class="mt-3">
          <a-tag>{{ item.status }}</a-tag>
          <a-tag>{{ item.source_type }}</a-tag>
          <a-tag>{{ item.tags }}</a-tag>
        </div>
      </a-card>
    </div>
  </div>
</template>

