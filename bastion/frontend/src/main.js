import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import Login from './views/Login.vue'
import Assets from './views/Assets.vue'
import Terminal from './views/Terminal.vue'
import Desktop from './views/Desktop.vue'
import Sessions from './views/Sessions.vue'
import './style.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: Login, meta: { public: true } },
    { path: '/', component: Assets },
    { path: '/terminal/:id', component: Terminal, props: true },
    { path: '/desktop/:id', component: Desktop, props: true },
    { path: '/sessions', component: Sessions },
  ],
})

router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (!to.meta.public && !token) return '/login'
  if (to.path === '/login' && token) return '/'
})

createApp(App).use(router).mount('#app')
