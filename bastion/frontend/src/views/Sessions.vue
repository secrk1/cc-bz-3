<template>
  <AppLayout>
    <h1>会话与审计</h1>
    <div class="card" style="margin-bottom:18px">
      <h2>会话记录</h2>
      <table>
        <thead>
          <tr>
            <th>会话 ID</th><th>协议</th><th>资产</th><th>状态</th>
            <th>来源 IP</th><th>开始时间</th><th>结束时间</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in sessions" :key="s.id">
            <td><code>{{ s.id.slice(0, 12) }}</code></td>
            <td><span class="badge" :class="s.protocol">{{ s.protocol.toUpperCase() }}</span></td>
            <td>{{ assetName(s.asset_id) }}</td>
            <td><span class="badge" :class="s.status">{{ s.status }}</span></td>
            <td>{{ s.client_ip || '-' }}</td>
            <td>{{ fmt(s.started_at) }}</td>
            <td>{{ s.ended_at ? fmt(s.ended_at) : '-' }}</td>
            <td>
              <button class="btn ghost" @click="viewAudit(s)">审计</button>
            </td>
          </tr>
          <tr v-if="!sessions.length"><td colspan="8" class="muted">暂无会话</td></tr>
        </tbody>
      </table>
    </div>

    <div v-if="current" class="card">
      <h2>指令审计 · {{ current.id.slice(0, 12) }}
        <button v-if="current.protocol === 'ssh'" class="btn ghost" style="float:right"
                @click="downloadCast(current)">下载录屏 (.cast)</button>
      </h2>
      <table>
        <thead><tr><th style="width:180px">时间</th><th style="width:90px">事件</th><th>内容</th></tr></thead>
        <tbody>
          <tr v-for="a in audits" :key="a.id">
            <td>{{ fmt(a.created_at) }}</td>
            <td><span class="badge">{{ a.event_type }}</span></td>
            <td><code>{{ a.content || '' }}</code></td>
          </tr>
          <tr v-if="!audits.length"><td colspan="3" class="muted">暂无审计记录</td></tr>
        </tbody>
      </table>
    </div>
  </AppLayout>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import api from '../api'

const sessions = ref([])
const assets = ref([])
const current = ref(null)
const audits = ref([])

function assetName(id) {
  return assets.value.find((a) => a.id === id)?.name || `#${id}`
}
function fmt(t) {
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

async function viewAudit(s) {
  current.value = s
  const { data } = await api.get(`/api/sessions/${s.id}/audit`)
  audits.value = data
}

function downloadCast(s) {
  // 带 JWT 下载：通过 fetch 拿 blob
  fetch(`/api/sessions/${s.id}/recording`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('token')}` },
  }).then(async (r) => {
    if (!r.ok) return alert('该会话暂无录屏文件')
    const blob = await r.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${s.id}.cast`
    a.click()
    URL.revokeObjectURL(url)
  })
}

onMounted(async () => {
  const [sess, assetList] = await Promise.all([
    api.get('/api/sessions'), api.get('/api/assets'),
  ])
  sessions.value = sess.data
  assets.value = assetList.data
})
</script>
