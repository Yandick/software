import type { RouteRecordRaw } from 'vue-router';

const routes: RouteRecordRaw[] = [
  {
    meta: {
      icon: 'lucide:bot',
      order: -10,
      title: '运维数字员工',
    },
    name: 'OpsEmployee',
    path: '/ops',
    redirect: '/ops/dashboard',
    children: [
      {
        name: 'OpsDashboard',
        path: '/ops/dashboard',
        component: () => import('#/views/ops/dashboard/index.vue'),
        meta: {
          affixTab: true,
          icon: 'lucide:sparkles',
          title: 'AI 助手',
        },
      },
      {
        name: 'OpsIssues',
        path: '/ops/issues',
        component: () => import('#/views/ops/issues/index.vue'),
        meta: {
          icon: 'lucide:clipboard-list',
          title: '在线记录',
        },
      },
      {
        name: 'OpsAccounts',
        path: '/ops/accounts',
        component: () => import('#/views/ops/accounts/index.vue'),
        meta: {
          icon: 'lucide:user-cog',
          title: '账号管理',
        },
      },
      {
        name: 'OpsKnowledge',
        path: '/ops/knowledge',
        component: () => import('#/views/ops/knowledge/index.vue'),
        meta: {
          icon: 'lucide:book-open',
          title: '知识库',
        },
      },
      {
        name: 'OpsAudit',
        path: '/ops/audit',
        component: () => import('#/views/ops/audit/index.vue'),
        meta: {
          icon: 'lucide:shield-check',
          title: '统计审计',
        },
      },
    ],
  },
];

export default routes;
