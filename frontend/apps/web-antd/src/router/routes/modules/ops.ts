import type { RouteRecordRaw } from 'vue-router';

const routes: RouteRecordRaw[] = [
  {
    meta: {
      authority: ['admin', 'ops', 'auditor'],
      icon: 'lucide:bot',
      order: -10,
      title: '数字员工服务台',
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
          authority: ['admin', 'ops', 'auditor'],
          affixTab: true,
          icon: 'lucide:message-circle',
          title: '智能服务台',
        },
      },
      {
        name: 'OpsIssues',
        path: '/ops/issues',
        component: () => import('#/views/ops/issues/index.vue'),
        meta: {
          authority: ['admin', 'ops', 'auditor'],
          icon: 'lucide:clipboard-list',
          title: '在线记录',
        },
      },
      {
        name: 'OpsAccounts',
        path: '/ops/accounts',
        component: () => import('#/views/ops/accounts/index.vue'),
        meta: {
          authority: ['admin', 'ops', 'auditor'],
          icon: 'lucide:user-cog',
          title: '账号管理',
        },
      },
      {
        name: 'OpsKnowledge',
        path: '/ops/knowledge',
        component: () => import('#/views/ops/knowledge/index.vue'),
        meta: {
          authority: ['admin', 'ops'],
          icon: 'lucide:book-open',
          title: '知识库维护',
        },
      },
      {
        name: 'OpsAudit',
        path: '/ops/audit',
        component: () => import('#/views/ops/audit/index.vue'),
        meta: {
          authority: ['admin', 'auditor'],
          icon: 'lucide:shield-check',
          title: '统计审计',
        },
      },
    ],
  },
];

export default routes;
