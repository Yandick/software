import { defineOverridesPreferences } from '@vben/preferences';

/**
 * @description 项目配置文件
 * 只需要覆盖项目中的一部分配置，不需要的配置不用覆盖，会自动使用默认配置
 * !!! 更改配置后请清空缓存，否则可能不生效
 */
export const overridesPreferences = defineOverridesPreferences({
  app: {
    defaultHomePath: '/portal',
    enableCheckUpdates: false,
    enableCopyPreferences: false,
    name: import.meta.env.VITE_APP_TITLE,
  },
  copyright: {
    enable: false,
  },
  theme: {
    builtinType: 'deep-green',
    colorDestructive: 'hsl(348 83% 47%)',
    colorPrimary: 'hsl(176 84% 32%)',
    colorSuccess: 'hsl(160 84% 39%)',
    colorWarning: 'hsl(35 92% 50%)',
    mode: 'light',
    radius: '0.5',
  },
});
