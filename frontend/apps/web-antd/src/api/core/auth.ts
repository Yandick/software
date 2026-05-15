import { requestClient } from '#/api/request';

export namespace AuthApi {
  /** 登录接口参数 */
  export interface LoginParams {
    password?: string;
    username?: string;
  }

  /** 登录接口返回值 */
  export interface LoginResult {
    accessToken: string;
    user?: Record<string, any>;
  }

  export interface RefreshTokenResult {
    data: string;
    status: number;
  }
}

/**
 * 登录
 */
export async function loginApi(data: AuthApi.LoginParams) {
  const result = await requestClient.post<{
    access_token: string;
    user: Record<string, any>;
  }>('/auth/login', data);
  return {
    accessToken: result.access_token,
    user: result.user,
  };
}

/**
 * 刷新accessToken
 */
export async function refreshTokenApi() {
  return requestClient.post<AuthApi.RefreshTokenResult>('/auth/refresh');
}

/**
 * 退出登录
 */
export async function logoutApi() {
  return Promise.resolve();
}

/**
 * 获取用户权限码
 */
export async function getAccessCodesApi() {
  return Promise.resolve(['AC_100100', 'AC_100110', 'AC_100120']);
}
