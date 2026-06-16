<script lang="ts" setup>
import type { VbenFormSchema } from '@vben/common-ui';

import { computed } from 'vue';
import { useRouter } from 'vue-router';

import { AuthenticationLogin, z } from '@vben/common-ui';
import { $t } from '@vben/locales';

import { message } from 'ant-design-vue';

import { useAuthStore } from '#/store';

defineOptions({ name: 'Login' });

const authStore = useAuthStore();
const router = useRouter();

// TODO(auth-roadmap): 当前仅开放账号密码登录。手机号/扫码/第三方登录/注册/忘记密码
// 需要后端接口、审计和安全校验完成后再打开，详见 docs/auth-roadmap.md。

const formSchema = computed((): VbenFormSchema[] => {
  return [
    {
      component: 'VbenInput',
      componentProps: {
        placeholder: $t('authentication.usernameTip'),
      },
      fieldName: 'username',
      label: $t('authentication.username'),
      rules: z.string().min(1, { message: $t('authentication.usernameTip') }),
    },
    {
      component: 'VbenInputPassword',
      componentProps: {
        placeholder: $t('authentication.password'),
      },
      fieldName: 'password',
      label: $t('authentication.password'),
      rules: z.string().min(1, { message: $t('authentication.passwordTip') }),
    },
  ];
});

async function handleSubmit(values: Record<string, any>) {
  const result = await authStore.authLogin(values, async () => {});
  const role = result.userInfo?.roles?.[0];
  if (role === 'user') {
    message.info('普通用户已进入服务门户');
    await router.replace('/portal');
    return;
  }
  await router.replace('/ops/dashboard');
}
</script>

<template>
  <AuthenticationLogin
    :form-schema="formSchema"
    :loading="authStore.loginLoading"
    :show-code-login="false"
    :show-forget-password="false"
    :show-qrcode-login="false"
    :show-register="false"
    :show-third-party-login="false"
    sub-title="运维、管理员和审计员从这里进入后台；普通用户请从服务门户发起咨询和在线记录"
    submit-button-text="进入工作人员管理台"
    title="工作人员管理台"
    @submit="handleSubmit"
  >
    <template #to-register>
      <div class="mt-4 rounded-lg bg-muted px-4 py-3 text-sm leading-6">
        <div class="font-medium">工作人员体验账号</div>
        <div>管理员：admin / admin123</div>
        <div>运维人员：ops / ops123</div>
        <div>审计员：auditor / audit123</div>
        <button class="mt-3 text-primary" type="button" @click="router.push('/portal')">
          返回用户服务门户
        </button>
      </div>
    </template>
  </AuthenticationLogin>
</template>
