<template>
  <div class="terminal-page">
    <div class="topbar">
      <span>🖥️ SSH 终端 · {{ asset?.name || '加载中…' }}</span>
      <span class="muted">{{ asset ? `${asset.username}@${asset.host}:${asset.port}` : '' }}</span>
      <div class="spacer"></div>
      <span class="toast">{{ status }}</span>
      <button class="btn ghost" @click="$router.push('/')">断开返回</button>
    </div>
    <div id="terminal"></div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Terminal } from 'xterm'
import { FitAddon } from 'xterm-addon-fit'
import 'xterm/css/xterm.css'
import api, { wsUrl } from '../api'

const props = defineProps({ id: { type: [String, Number], required: true } })
const asset = ref(null)
const status = ref('正在连接…')

let term, fit, ws, resizeTimer

function sendResize() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return
  const dims = fit.proposeDimensions()
  if (!dims) return
  ws.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }))
}

onMounted(async () => {
  const list = await api.get('/api/assets')
  asset.value = list.data.find((x) => String(x.id) === String(props.id))

  term = new Terminal({
    fontFamily: 'Menlo, Consolas, monospace', fontSize: 14,
    cursorBlink: true, theme: { background: '#0b1020' },
  })
  fit = new FitAddon()
  term.loadAddon(fit)
  term.open(document.getElementById('terminal'))
  fit.fit()

  const dims = fit.proposeDimensions()
  const url = `${wsUrl(`/ws/ssh/${props.id}`)}&cols=${dims.cols}&rows=${dims.rows}`
  ws = new WebSocket(url)
  ws.binaryType = 'arraybuffer'

  ws.onopen = () => { status.value = '已连接'; sendResize() }
  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) term.write(new Uint8Array(ev.data))
    else term.write(ev.data)
  }
  ws.onclose = () => {
    status.value = '连接已关闭'
    term.write('\r\n\x1b[33m[会话结束]\x1b[0m\r\n')
  }
  ws.onerror = () => { status.value = '连接错误' }

  term.onData((data) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(new TextEncoder().encode(data))
    }
  })

  window.addEventListener('resize', onResize)
})

function onResize() {
  clearTimeout(resizeTimer)
  resizeTimer = setTimeout(() => { fit?.fit(); sendResize() }, 120)
}

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  ws?.close()
  term?.dispose()
})
</script>
