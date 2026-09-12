<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">🛡️ 轻量堡垒机</div>
      <router-link to="/">资产主机</router-link>
      <router-link to="/sessions">会话审计</router-link>
      <div class="spacer"></div>
      <div class="user">当前用户：{{ username }}</div>
      <div style="padding:0 6px;">
        <button @click="logout">退出登录</button>
      </div>
    </aside>
    <main class="content">
      <slot />
    </main>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import api from '../api'

const username = ref('')
onMounted(async () => {
  try {
    const { data } = await api.get('/api/auth/me')
    username.value = data.username
  } catch { /* 拦截器处理 */ }
})

function logout() {
  localStorage.removeItem('token')
  location.href = '/login'
}
</script>
