import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import Login from './views/Login.vue'
import Assets from './views/Assets.vue'
import Terminal from './views/Terminal.vue'
import Desktop from './views/Desktop.vue'
import Sessions from './views/Sessions.vue'
import Observe from './views/Observe.vue'
import Files from './views/Files.vue'
import Transfers from './views/Transfers.vue'
import { isAdmin } from './api'
import './style.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: Login, meta: { public: true } },
    { path: '/', component: Assets },
    { path: '/terminal/:id', component: Terminal, props: true },
    { path: '/desktop/:id', component: Desktop, props: true },
    { path: '/files/:id', component: Files, props: true },
    // 文件传输审计为管理员专属
    { path: '/transfers', component: Transfers, meta: { admin: true } },
    // 会话审计/旁观/强踢为管理员专属
    { path: '/sessions', component: Sessions, meta: { admin: true } },
    { path: '/observe/:id', component: Observe, props: true, meta: { admin: true } },
  ],
})

router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (!to.meta.public && !token) return '/login'
  if (to.path === '/login' && token) return '/'
  // 非管理员直接访问会话管理路由时回到资产页
  if (to.meta.admin && !isAdmin()) return '/'
})

createApp(App).use(router).mount('#app')
