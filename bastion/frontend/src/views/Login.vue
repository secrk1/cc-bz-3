<template>
  <div class="login-wrap">
    <div class="card login-card">
      <h1>🛡️ 轻量堡垒机</h1>
      <p class="muted" style="text-align:center">登录以纳管和接入远程主机</p>
      <form @submit.prevent="submit">
        <div class="field">
          <label>用户名</label>
          <input v-model="username" autocomplete="username" placeholder="admin" />
        </div>
        <div class="field">
          <label>密码</label>
          <input v-model="password" type="password" autocomplete="current-password"
                 placeholder="admin123" />
        </div>
        <div v-if="error" class="error">{{ error }}</div>
        <button class="btn" style="width:100%" :disabled="loading">
          {{ loading ? '登录中…' : '登 录' }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import api from '../api'

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    const { data } = await api.post('/api/auth/login', {
      username: username.value, password: password.value,
    })
    localStorage.setItem('token', data.access_token)
    location.href = '/'
  } catch (e) {
    error.value = e.response?.data?.detail || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>
