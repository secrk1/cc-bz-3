<template>
  <div class="desktop-page">
    <div class="topbar">
      <span>🖼️ RDP 远程桌面 · {{ asset?.name || '加载中…' }}</span>
      <span class="muted">{{ asset ? `${asset.username}@${asset.host}:${asset.port}` : '' }}</span>
      <div class="spacer"></div>
      <span class="toast">{{ status }}</span>
      <button class="btn ghost" @click="$router.push('/')">断开返回</button>
    </div>
    <div id="desktop" ref="desktopEl"></div>
    <div v-if="errorMsg" class="error-overlay">
      <div class="error-box">
        <h3>RDP 连接失败</h3>
        <p>{{ errorMsg }}</p>
        <p class="muted">请在资产页用「连通性」查看详细探测结果。</p>
        <button class="btn" @click="$router.push('/')">返回资产列表</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import Guacamole from 'guacamole-common-js'
import api from '../api'

const props = defineProps({ id: { type: [String, Number], required: true } })
const asset = ref(null)
const status = ref('正在连接…')
const errorMsg = ref('')
const desktopEl = ref(null)

let client, keyboard, mouse, resizeTimer

onMounted(async () => {
  const list = await api.get('/api/assets')
  asset.value = list.data.find((x) => String(x.id) === String(props.id))

  const el = desktopEl.value
  const w = Math.max(800, el.clientWidth)
  const h = Math.max(600, el.clientHeight)
  const token = encodeURIComponent(localStorage.getItem('token') || '')
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'

  // 服务端隧道：目标资产由后端依据 asset id 决定，浏览器仅回传 JWT 与期望分辨率
  const tunnel = new Guacamole.WebSocketTunnel(
    `${proto}://${location.host}/ws/rdp/${props.id}`,
  )
  client = new Guacamole.Client(tunnel)

  client.onstateChange = (state) => {
    const map = {
      0: '空闲', 1: '连接中', 2: '等待中', 3: '已连接', 4: '断开中', 5: '已断开',
    }
    status.value = map[state] ?? `状态 ${state}`
    if (state === 5) status.value = '连接已关闭'
  }
  client.onerror = (e) => {
    if (e?.message) {
      status.value = `错误：${e.message}`
      errorMsg.value = e.message
    }
  }

  const display = client.getDisplay()
  el.appendChild(client.getDisplay().getElement())
  display.onresize = (dw, dh) => {
    // 远端分辨率变化时按容器等比缩放
    const scale = Math.min(el.clientWidth / dw, el.clientHeight / dh, 1)
    display.scale(scale)
  }

  // 鼠标
  mouse = new Guacamole.Mouse(display.getElement())
  mouse.onmousedown = mouse.onmouseup = mouse.onmousemove = (s) => client.sendMouseState(s)

  // 键盘
  keyboard = new Guacamole.Keyboard(document)
  keyboard.onkeydown = (keysym) => client.sendKeyEvent(1, keysym)
  keyboard.onkeyup = (keysym) => client.sendKeyEvent(0, keysym)

  // connect 的参数会作为 WS 查询串：token + 期望分辨率
  client.connect(`token=${token}&width=${Math.round(w)}&height=${Math.round(h)}`)

  window.addEventListener('resize', onResize)
})

function onResize() {
  clearTimeout(resizeTimer)
  resizeTimer = setTimeout(() => {
    const el = desktopEl.value
    if (!client || !el) return
    // 通知 guacd 调整远端分辨率（需 resize-method=display-update）
    client.sendSize(Math.round(el.clientWidth), Math.round(el.clientHeight))
  }, 200)
}

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  try {
    if (keyboard) { keyboard.onkeydown = null; keyboard.onkeyup = null }
    client?.disconnect()
  } catch { /* ignore */ }
})
</script>
