<template>
  <AppLayout>
    <div class="toolbar">
      <h1 style="margin:0">资产主机</h1>
      <button class="btn" @click="showCreate = true">+ 新增资产</button>
    </div>

    <div class="card">
      <table>
        <thead>
          <tr>
            <th>名称</th><th>协议</th><th>地址</th><th>账号</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="a in assets" :key="a.id">
            <tr>
              <td>{{ a.name }}</td>
              <td><span class="badge" :class="a.protocol">{{ a.protocol.toUpperCase() }}</span></td>
              <td>{{ a.host }}:{{ a.port }}</td>
              <td>{{ a.username }}</td>
              <td>
                <a v-if="a.protocol === 'ssh'" class="btn" target="_blank"
                   rel="noopener" :href="`/terminal/${a.id}`">SSH 终端 ↗</a>
                <a v-else class="btn" target="_blank"
                   rel="noopener" :href="`/desktop/${a.id}`">RDP 桌面 ↗</a>
                <button class="btn ghost" style="margin-left:8px"
                        @click="check(a)" :disabled="checking[a.id]">
                  {{ checking[a.id] ? '探测中…' : '连通性' }}
                </button>
                <button v-if="isAdmin" class="btn danger" style="margin-left:8px"
                        @click="remove(a)">删除</button>
              </td>
            </tr>
            <tr v-if="result[a.id]">
              <td colspan="5" style="padding:4px 12px 12px">
                <span :style="{ color: result[a.id].ok ? 'var(--ok)' : 'var(--danger)', fontSize: '13px' }">
                  {{ result[a.id].ok ? '✓ ' : '✗ ' }}{{ result[a.id].detail }}
                </span>
              </td>
            </tr>
          </template>
          <tr v-if="!assets.length">
            <td colspan="5" class="muted">暂无资产</td>
          </tr>
        </tbody>
      </table>
    </div>

    <dialog v-if="showCreate" open @close="showCreate = false">
      <form method="dialog" @submit.prevent="create">
        <h2 style="color:var(--text)">新增资产</h2>
        <div class="field">
          <label>名称</label>
          <input v-model="form.name" required placeholder="生产-web-01" />
        </div>
        <div class="field">
          <label>协议</label>
          <select v-model="form.protocol" @change="onProtocolChange">
            <option value="ssh">SSH</option>
            <option value="rdp">RDP</option>
          </select>
        </div>
        <div class="field">
          <label>主机</label>
          <input v-model="form.host" required placeholder="10.0.0.5 或主机名" />
        </div>
        <div class="field">
          <label>端口</label>
          <input v-model.number="form.port" type="number" min="1" max="65535" required />
        </div>
        <div class="field">
          <label>用户名</label>
          <input v-model="form.username" required />
        </div>
        <div class="field">
          <label>密码 / 私钥（留空表示免密）</label>
          <input v-model="form.secret" :type="form.protocol === 'rdp' ? 'password' : 'text'" />
        </div>
        <p v-if="formError" class="error">{{ formError }}</p>
        <div style="display:flex;gap:10px;justify-content:flex-end">
          <button type="button" class="btn ghost" @click="showCreate = false">取消</button>
          <button class="btn">保存</button>
        </div>
      </form>
    </dialog>
  </AppLayout>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import api from '../api'

const assets = ref([])
const isAdmin = ref(false)
const showCreate = ref(false)
const checking = reactive({})   // { [id]: bool }
const result = reactive({})     // { [id]: { ok, detail } }
const formError = ref('')
const form = reactive({
  name: '', protocol: 'ssh', host: '', port: 22, username: 'root', secret: '',
})

async function load() {
  const { data } = await api.get('/api/assets')
  assets.value = data
  const me = await api.get('/api/auth/me')
  isAdmin.value = me.data.is_admin
}

async function create() {
  formError.value = ''
  try {
    await api.post('/api/assets', { ...form, secret: form.secret || null })
    showCreate.value = false
    Object.assign(form, { name: '', host: '', port: 22, username: 'root', secret: '' })
    await load()
  } catch (e) {
    formError.value = e.response?.data?.detail || '创建失败'
  }
}

async function remove(a) {
  if (!confirm(`确定删除资产「${a.name}」？`)) return
  await api.delete(`/api/assets/${a.id}`)
  await load()
}

function onProtocolChange() {
  form.port = form.protocol === 'ssh' ? 22 : 3389
}

async function check(a) {
  checking[a.id] = true
  result[a.id] = { ok: true, detail: '正在探测…' }
  try {
    const { data } = await api.post(`/api/assets/${a.id}/check`)
    result[a.id] = data
  } catch (e) {
    result[a.id] = {
      ok: false,
      detail: e.response?.data?.detail || '探测请求失败',
    }
  } finally {
    checking[a.id] = false
  }
}

onMounted(load)
</script>
