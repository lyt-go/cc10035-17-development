import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 批次受理回执是报文解码工作台内的能力，这些常见路径都落到同一工作台。
    { path: '/msg_decode/batch', name: 'msg-decode-batch', component: WorkbenchView, props: { moduleKey: 'msg_decode' } },
    { path: '/msg_decode/batch-receipt', name: 'msg-decode-batch-receipt', component: WorkbenchView, props: { moduleKey: 'msg_decode' } },
    { path: '/msg_decode/batch-receipts', name: 'msg-decode-batch-receipts', component: WorkbenchView, props: { moduleKey: 'msg_decode' } },
    { path: '/batch-receipts', name: 'batch-receipts', component: WorkbenchView, props: { moduleKey: 'msg_decode' } },
    { path: '/batch-receipt', name: 'batch-receipt', component: WorkbenchView, props: { moduleKey: 'msg_decode' } },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
