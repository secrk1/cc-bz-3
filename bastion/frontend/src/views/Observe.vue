<template>
  <div class="observe-page">
    <div class="topbar">
      <span>👁 实时旁观 · {{ info ? info.asset_name : '加载中…' }}</span>
      <span class="muted" v-if="info">
        {{ info.protocol.toUpperCase() }} · 用户 {{ info.user_name }} · {{ info.client_ip || '-' }}
      </span>
      <div class="spacer"></div>
      <span class="toast" :class="{ ended: ended }">{{ status }}</span>
      <button class="btn danger" :disabled="ended || kicking"
              @click="kick">
        {{ kicking ? '处理中…' : '⛔ 强踢下线' }}
      </button>
      <button class="btn ghost" @click="$router.push('/sessions')">返回</button>
    </div>

    <div v-if="offline" class="error-overlay">
      <div class="error-box">
        <h3>该会话不在线</h3>
        <p>实时旁观仅支持进行中的会话。该会话已结束，可在会话审计页回放其录像。</p>
        <button class="btn" @click="$router.push('/sessions')">返回会话审计</button>
      </div>
    </div>

    <!-- SSH：只读终端镜像 -->
    <div v-show="info?.protocol === 'ssh'" id="observe-terminal"></div>
    <!-- RDP：只读桌面镜像 -->
    <div v-show="info?.protocol === 'rdp'" id="observe-desktop" ref="desktopEl"></div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Terminal } from 'xterm'
import { FitAddon } from 'xterm-addon-fit'
import Guacamole from 'guacamole-common-js'
import 'xterm/css/xterm.css'
import api, { wsUrl } from '../api'

const props = defineProps({ id: { type: String, required: true } })
const info = ref(null)
const status = ref('正在连接旁观通道…')
const ended = ref(false)
const offline = ref(false)
const kicking = ref(false)
const desktopEl = ref(null)

let ws, term, fit, client, resizeTimer

function markEnded(text) {
  ended.value = true
  status.value = text
  try { term?.write(`\r\n\x1b[33m${text}\x1b[0m\r\n`) } catch { /* ignore */ }
}

function kick() {
  if (!confirm('确定强制结束该用户的会话？对方将立即断开。')) return
  kicking.value = true
  api.post(`/api/sessions/${props.id}/kick`)
    .then(() => { status.value = '已发送强踢指令' })
    .catch((e) => {
      status.value = e.response?.data?.detail || '强踢失败'
    })
    .finally(() => { kicking.value = false })
}

function connectSsh() {
  term = new Terminal({
    fontFamily: 'Menlo, Consolas, monospace', fontSize: 14,
    cursorBlink: false, disableStdin: true, theme: { background: '#0b1020' },
  })
  fit = new FitAddon()
  term.loadAddon(fit)
  term.open(document.getElementById('observe-terminal'))
  fit.fit()

  ws = new WebSocket(wsUrl(`/ws/observe/${props.id}`))
  ws.binaryType = 'arraybuffer'
  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) term.write(new Uint8Array(ev.data))
    else term.write(ev.data)
  }
  ws.onclose = () => { if (!ended.value) markEnded('旁观通道已关闭') }
  ws.onerror = () => { status.value = '旁观连接错误' }

  window.addEventListener('resize', onResize)
}

function connectRdp() {
  const el = desktopEl.value
  const tunnel = new Guacamole.WebSocketTunnel(
    `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}` +
    `/ws/observe/${props.id}`,
  )
  client = new Guacamole.Client(tunnel)
  client.onstateChange = (s) => {
    status.value = { 0: '空闲', 1: '连接中', 2: '等待中', 3: '旁观中',
                     4: '断开中', 5: '已结束' }[s] ?? `状态 ${s}`
    if (s === 5 && !ended.value) markEnded('会话已结束')
  }
  client.onerror = (e) => {
    if (/强制结束|结束/.test(e?.message || '')) markEnded(e.message)
    else status.value = e?.message ? `错误：${e.message}` : '旁观错误'
  }
  const display = client.getDisplay()
  el.appendChild(display.getElement())
  display.onresize = (dw, dh) => {
    const scale = Math.min(el.clientWidth / dw, el.clientHeight / dh, 1)
    display.scale(scale)
  }
  // 只读旁观：不绑定 Mouse/Keyboard；token 经 connect 查询串传递
  const token = encodeURIComponent(localStorage.getItem('token') || '')
  client.connect(`token=${token}`)
}

function onResize() {
  clearTimeout(resizeTimer)
  resizeTimer = setTimeout(() => { try { fit?.fit() } catch { /* ignore */ } }, 120)
}

onMounted(async () => {
  try {
    const { data } = await api.get(`/api/sessions/${props.id}`)
    info.value = data
    if (data.status !== 'active') {
      offline.value = true
      return
    }
    status.value = '旁观中'
    if (data.protocol === 'ssh') connectSsh()
    else if (data.protocol === 'rdp') connectRdp()
  } catch {
    offline.value = true
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  try { ws?.close() } catch { /* ignore */ }
  try { client?.disconnect() } catch { /* ignore */ }
  term?.dispose()
})
</script>

<style scoped>
.observe-page {
  position: fixed; inset: 0; background: #000;
  display: flex; flex-direction: column;
}
#observe-terminal, #observe-desktop { flex: 1; overflow: hidden; }
#observe-desktop canvas { display: block; }
.toast.ended { color: var(--warning, #fbbf24); }
</style>
