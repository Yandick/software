<script lang="ts" setup>
import type { VbenFormSchema } from '@vben/common-ui';

import { computed } from 'vue';

import { AuthenticationLogin, z } from '@vben/common-ui';
import { $t } from '@vben/locales';

import { useAuthStore } from '#/store';

defineOptions({ name: 'Login' });

const authStore = useAuthStore();

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
    sub-title="请使用已分配的系统账号登录，所有操作均按角色权限和审计规则执行"
    submit-button-text="登录运维数字员工系统"
    title="运维数字员工系统"
    @submit="authStore.authLogin"
  >
    <template #to-register>
      <div class="mt-4 rounded-lg bg-muted px-4 py-3 text-sm leading-6">
        <div class="font-medium">内置体验账号</div>
        <div>管理员：admin / admin123</div>
        <div>运维人员：ops / ops123</div>
        <div>普通用户：user / user123</div>
        <div>审计员：auditor / audit123</div>
      </div>
    </template>
  </AuthenticationLogin>
</template>
