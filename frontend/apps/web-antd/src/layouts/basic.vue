<script lang="ts" setup>
import { computed, watch } from 'vue';
import { useRouter } from 'vue-router';

import { AuthenticationLoginExpiredModal } from '@vben/common-ui';
import { useWatermark } from '@vben/hooks';
import { IconifyIcon } from '@vben/icons';
import { BasicLayout, LockScreen, UserDropdown } from '@vben/layouts';
import { preferences, usePreferences } from '@vben/preferences';
import { useAccessStore, useUserStore } from '@vben/stores';

import { useAuthStore } from '#/store';

const userStore = useUserStore();
const authStore = useAuthStore();
const accessStore = useAccessStore();
const router = useRouter();
const { destroyWatermark, updateWatermark } = useWatermark();
const { isDark } = usePreferences();
const menus: Array<{ handler: () => void; icon?: string; text: string }> = [];

const avatar = computed(() => {
  return userStore.userInfo?.avatar ?? preferences.app.defaultAvatar;
});
const userDescription = computed(() => userStore.userInfo?.username ?? '');
const userTagText = computed(() => userStore.userInfo?.roles?.[0] ?? '');

async function handleLogout() {
  await authStore.logout(false, '/portal');
}

async function goPortal() {
  await router.push('/portal');
}

async function returnPortalHome() {
  await authStore.logout(false, '/portal');
}

async function switchIdentity() {
  await authStore.logout(false, '/portal?identity=user');
}

watch(
  () => ({
    enable: preferences.app.watermark,
    content: preferences.app.watermarkContent,
    isDark: isDark.value,
  }),
  async ({ enable, content, isDark: isDarkValue }) => {
    if (enable) {
      const watermarkColor = isDarkValue
        ? 'rgba(255, 255, 255, 0.12)'
        : 'rgba(0, 0, 0, 0.12)';

      await updateWatermark({
        advancedStyle: {
          colorStops: [
            {
              color: watermarkColor,
              offset: 0,
            },
            {
              color: watermarkColor,
              offset: 1,
            },
          ],
          type: 'linear',
        },
        content:
          content ||
          `${userStore.userInfo?.username} - ${userStore.userInfo?.realName}`,
      });
    } else {
      destroyWatermark();
    }
  },
  {
    immediate: true,
  },
);
</script>

<template>
  <BasicLayout @clear-preferences-and-logout="handleLogout">
    <template #header-right-80>
      <div class="staff-identity-actions">
        <button type="button" @click="goPortal">
          <IconifyIcon icon="lucide:home" />
          <span>用户门户</span>
        </button>
        <button type="button" @click="returnPortalHome">
          <IconifyIcon icon="lucide:log-out" />
          <span>门户首页</span>
        </button>
        <button class="primary" type="button" @click="switchIdentity">
          <IconifyIcon icon="lucide:shuffle" />
          <span>切换身份</span>
        </button>
      </div>
    </template>
    <template #user-dropdown>
      <UserDropdown
        :avatar
        :menus
        :text="userStore.userInfo?.realName"
        :description="userDescription"
        :tag-text="userTagText"
        @logout="handleLogout"
      />
    </template>
    <template #extra>
      <AuthenticationLoginExpiredModal
        v-model:open="accessStore.loginExpired"
        :avatar
      >
        <div class="login-expired-portal">
          <strong>登录状态已过期</strong>
          <span>请返回门户重新选择用户或工作人员身份。</span>
          <button type="button" @click="returnPortalHome">返回门户登录</button>
        </div>
      </AuthenticationLoginExpiredModal>
    </template>
    <template #lock-screen>
      <LockScreen :avatar @to-login="handleLogout" />
    </template>
  </BasicLayout>
</template>

<style scoped>
.staff-identity-actions {
  align-items: center;
  background: #f1f5f9;
  border: 1px solid rgb(15 23 42 / 9%);
  border-radius: 8px;
  display: flex;
  gap: 4px;
  margin-right: 10px;
  padding: 4px;
}

.staff-identity-actions button {
  align-items: center;
  background: transparent;
  border: 0;
  border-radius: 7px;
  color: #334155;
  cursor: pointer;
  display: inline-flex;
  font-size: 13px;
  gap: 6px;
  min-height: 32px;
  padding: 0 10px;
}

.staff-identity-actions button:hover {
  background: #fff;
  color: #0f766e;
}

.staff-identity-actions button.primary {
  background: #0f766e;
  color: #fff;
}

.login-expired-portal {
  align-items: stretch;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px 0 2px;
}

.login-expired-portal strong {
  color: #0f172a;
  font-size: 16px;
}

.login-expired-portal span {
  color: #64748b;
  font-size: 14px;
  line-height: 1.7;
}

.login-expired-portal button {
  background: #0f766e;
  border: 0;
  border-radius: 8px;
  color: #fff;
  cursor: pointer;
  min-height: 38px;
}

@media (max-width: 860px) {
  .staff-identity-actions button span {
    display: none;
  }
}
</style>
