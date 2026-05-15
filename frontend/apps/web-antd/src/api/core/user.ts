import type { UserInfo } from '@vben/types';

import { requestClient } from '#/api/request';

/**
 * 获取用户信息
 */
export async function getUserInfoApi() {
  const user = await requestClient.get<any>('/auth/me');
  return {
    ...user,
    avatar: '',
    desc: user.department,
    homePath: '/ops/dashboard',
    realName: user.real_name,
    roles: [user.role],
    userId: user.id,
    username: user.username,
  } as UserInfo;
}
