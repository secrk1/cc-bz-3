<template>
  <AppLayout>
    <div class="toolbar">
      <h1 style="margin:0">会话审计与回放</h1>
      <div class="filters">
        <select v-model="fStatus" @change="loadSessions">
          <option value="">全部状态</option>
          <option value="active">在线</option>
          <option value="closed">离线</option>
        </select>
        <select v-model="fProtocol" @change="loadSessions">
          <option value="">全部协议</option>
          <option value="ssh">SSH</option>
          <option value="rdp">RDP</option>
          <option value="tcp">TCP</option>
        </select>
        <button class="btn ghost" @click="loadSessions">刷新</button>
      </div>
    </div>

    <div class="card" style="margin-bottom:18px">
      <h2>会话列表 <span class="muted" style="text-transform:none">· 每 5 秒自动刷新在线状态</span></h2>
      <table>
        <thead>
          <tr>
            <th>会话 ID</th><th>用户</th><th>协议</th><th>目标资产</th><th>状态</th>
            <th>来源 IP</th><th>开始时间</th><th>结束时间</th><th style="width:300px"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in sessions" :key="s.id"
              :class="{ current: current?.id === s.id }">
            <td><code>{{ s.id.slice(0, 12) }}</code></td>
            <td>{{ s.user_name }}</td>
            <td><span class="badge" :class="s.protocol">{{ s.protocol.toUpperCase() }}</span></td>
            <td>{{ s.asset_name }}</td>
            <td>
              <span class="status-dot" :class="s.status"></span>
              {{ s.status === 'active' ? '在线' : '离线' }}
            </td>
            <td>{{ s.client_ip || '-' }}</td>
            <td>{{ fmt(s.started_at) }}</td>
            <td>{{ s.ended_at ? fmt(s.ended_at) : '-' }}</td>
            <td>
              <button class="btn ghost" @click="openDetail(s)">审计</button>
              <button class="btn" style="margin-left:6px"
                      :disabled="!canReplay(s)"
                      :title="replayHint(s)"
                      @click="openDetail(s, true)">▶ 回放</button>
              <template v-if="s.status === 'active' &&
                              (s.protocol === 'ssh' || s.protocol === 'rdp')">
                <button class="btn ghost" style="margin-left:6px"
                        @click="$router.push(`/observe/${s.id}`)">👁 旁观</button>
                <button class="btn danger" style="margin-left:6px"
                        :disabled="kickingId === s.id"
                        @click="kick(s)">
                  {{ kickingId === s.id ? '处理中…' : '强踢' }}
                </button>
              </template>
            </td>
          </tr>
          <tr v-if="!sessions.length"><td colspan="9" class="muted">暂无会话</td></tr>
        </tbody>
      </table>
    </div>

    <div v-if="current" class="card detail-card">
      <div class="detail-head">
        <h2 style="margin:0">
          会话 {{ current.id.slice(0, 12) }} · {{ current.asset_name }}
          <span class="badge" :class="current.protocol">{{ current.protocol.toUpperCase() }}</span>
          <span class="status-dot" :class="current.status"></span>
          <span class="muted" style="text-transform:none">
            {{ current.user_name }} · {{ current.client_ip || '-' }} ·
            {{ fmt(current.started_at) }} ~ {{ current.ended_at ? fmt(current.ended_at) : '进行中' }}
          </span>
        </h2>
        <div>
          <button v-if="canReplay(current)" class="btn ghost"
                  style="margin-right:8px" @click="downloadCast">
            下载录像 (.{{ current.protocol === 'ssh' ? 'cast' : 'guac' }})
          </button>
          <button class="btn ghost" @click="closeDetail">关闭</button>
        </div>
      </div>

      <div v-if="showPlayer" class="replay-wrap">
        <CastPlayer v-if="playerProtocol === 'ssh' && playerSessionId"
                    :key="'cast-' + playerSessionId"
                    ref="playerRef"
                    :session-id="playerSessionId"
                    :url="`/api/sessions/${playerSessionId}/recording`" />
        <GuacPlayer v-else-if="playerProtocol === 'rdp' && playerSessionId"
                    :key="'guac-' + playerSessionId"
                    ref="playerRef"
                    :session-id="playerSessionId"
                    :url="`/api/sessions/${playerSessionId}/recording`" />
      </div>
      <div v-else-if="current.protocol !== 'ssh' && current.protocol !== 'rdp'"
           class="muted replay-note">
        {{ current.protocol.toUpperCase() }} 会话为二进制中继通道，暂无录像。
      </div>
      <div v-else-if="current.status === 'active'" class="muted replay-note">
        该{{ current.protocol === 'ssh' ? ' SSH 终端' : ' RDP 桌面' }}会话仍在线，
        录像正在{{ current.protocol === 'ssh' ? '异步写入' : '由 guacd 录制' }}；
        会话结束后即可在此回放。
      </div>

      <h2 class="timeline-title">操作时间线（{{ audits.length }}）</h2>
      <table>
        <thead>
          <tr>
            <th style="width:170px">时间</th>
            <th style="width:120px; min-width:120px">事件</th>
            <th style="width:34%">操作指令</th>
            <th>响应摘要</th>
            <th v-if="showPlayer" style="width:70px"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in audits" :key="a.id">
            <td>{{ fmt(a.created_at) }}</td>
            <td><span class="badge" :class="a.event_type">{{ eventLabel(a.event_type) }}</span></td>
            <td><code>{{ a.content || '' }}</code></td>
            <td class="summary">{{ a.response_summary || '' }}</td>
            <td v-if="showPlayer">
              <button v-if="a.event_type === 'command'"
                      class="btn ghost btn-sm"
                      @click="jumpTo(a)">定位</button>
            </td>
          </tr>
          <tr v-if="!audits.length"><td :colspan="showPlayer ? 5 : 4" class="muted">暂无审计记录</td></tr>
        </tbody>
      </table>
    </div>
  </AppLayout>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import CastPlayer from '../components/CastPlayer.vue'
import GuacPlayer from '../components/GuacPlayer.vue'
import api from '../api'

const sessions = ref([])
const current = ref(null)
const audits = ref([])
const fStatus = ref('')
const fProtocol = ref('')
const showPlayer = ref(false)
const playerSessionId = ref('')
const playerProtocol = ref('')
const playerRef = ref(null)
const kickingId = ref('')
let pollTimer = null

function fmt(t) {
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}

function eventLabel(t) {
  return { login: '登录', command: '指令', close: '结束', kick: '强踢' }[t] || t
}

function canReplay(s) {
  return s.status === 'closed' &&
         (s.protocol === 'ssh' || s.protocol === 'rdp') &&
         !!s.recording_path
}

function replayHint(s) {
  if (s.protocol !== 'ssh' && s.protocol !== 'rdp') return '该协议无录像'
  if (s.status === 'active') return '会话在线，结束后才可回放'
  if (!s.recording_path) return '该会话无录屏文件'
  return s.protocol === 'ssh' ? '终端回放' : '桌面回放'
}

async function loadSessions() {
  try {
    const params = {}
    if (fStatus.value) params.status = fStatus.value
    if (fProtocol.value) params.protocol = fProtocol.value
    const { data } = await api.get('/api/sessions', { params })
    sessions.value = data
    // 若详情会话状态已变化（在线 -> 离线），同步引用，使“回放”按钮可用
    if (current.value) {
      const fresh = data.find((x) => x.id === current.value.id)
      if (fresh) current.value = fresh
    }
  } catch { /* 401 拦截器处理 */ }
}

async function openDetail(s, withPlayer = false) {
  current.value = s
  showPlayer.value = withPlayer && canReplay(s)
  playerSessionId.value = showPlayer.value ? s.id : ''
  playerProtocol.value = showPlayer.value ? s.protocol : ''
  const { data } = await api.get(`/api/sessions/${s.id}/audit`)
  audits.value = data
}

function closeDetail() {
  current.value = null
  showPlayer.value = false
  playerSessionId.value = ''
  playerProtocol.value = ''
  audits.value = []
}

function jumpTo(a) {
  // cast 时间轴零点近似为会话开始时间（header.timestamp 同源于会话建立时刻）
  const offset = (new Date(a.created_at) - new Date(current.value.started_at)) / 1000
  playerRef.value?.seekTo(Math.max(0, offset), true)
}

function downloadCast() {
  if (!current.value) return
  fetch(`/api/sessions/${current.value.id}/recording`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('token')}` },
  }).then(async (r) => {
    if (!r.ok) return
    const blob = await r.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${current.value.id}.${current.value.protocol === 'ssh' ? 'cast' : 'guac'}`
    a.click()
    URL.revokeObjectURL(url)
  })
}

async function kick(s) {
  if (!confirm(`确定强制结束 ${s.user_name} 对「${s.asset_name}」的${s.protocol.toUpperCase()}会话？`)) return
  kickingId.value = s.id
  try {
    await api.post(`/api/sessions/${s.id}/kick`)
    await loadSessions()
  } catch (e) {
    alert(e.response?.data?.detail || '强踢失败')
  } finally {
    kickingId.value = ''
  }
}

onMounted(() => {
  loadSessions()
  pollTimer = setInterval(loadSessions, 5000)
})

onBeforeUnmount(() => clearInterval(pollTimer))
</script>

<style scoped>
.filters { display: flex; gap: 10px; }
.filters select { width: 130px; }
tr.current { background: rgba(56, 189, 248, .08); }
.status-dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  margin-right: 5px; vertical-align: 1px;
}
.status-dot.active { background: var(--ok); box-shadow: 0 0 6px var(--ok); }
.status-dot.closed { background: var(--muted); }
.detail-card { padding-bottom: 26px; }
.detail-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.replay-wrap { margin: 16px 0 20px; }
.replay-note {
  margin: 14px 0; padding: 12px 14px; border: 1px dashed var(--border);
  border-radius: 8px;
}
.timeline-title { margin-top: 22px; }
.badge.command { color: #fcd34d; border-color: #92400e; }
.badge.login { color: var(--ok); border-color: #065f46; }
.badge.close { color: var(--muted); }
.badge.kick { color: var(--danger); border-color: #7f1d1d; }
.summary { color: var(--muted); font-size: 13px; word-break: break-word; }
.btn-sm { padding: 4px 9px; font-size: 12px; }
</style>
