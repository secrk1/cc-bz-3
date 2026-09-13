<template>
  <div class="guac-player">
    <div ref="hostEl" class="guac-screen"></div>
    <div class="guac-controls">
      <button class="pc-btn" :title="playing ? '暂停' : '播放'" @click="togglePlay">
        {{ playing ? '⏸' : '▶' }}
      </button>
      <button class="pc-btn" title="回到开头" @click="restart">⏮</button>
      <input class="pc-seek" type="range" min="0" :max="duration || 0"
             step="100" :value="position" @input="onSeek">
      <span class="pc-time">{{ fmt(position) }} / {{ fmt(duration) }}</span>
      <select v-model.number="speed" class="pc-speed" title="播放速度"
              @change="onSpeedChange">
        <option :value="0.5">0.5x</option>
        <option :value="1">1x</option>
        <option :value="2">2x</option>
        <option :value="4">4x</option>
      </select>
    </div>
    <div v-if="loading" class="muted" style="padding:8px 2px">图形录像加载中…</div>
    <div v-if="error" class="error" style="padding:8px 2px">{{ error }}</div>
  </div>
</template>

<script setup>
/* RDP 图形会话回放（基于 guacamole-common-js 的 SessionRecording）。
 *
 * 后端开启 guacd 原生录制，得到带 sync 时间戳的 Guacamole 指令流文件；
 * SessionRecording 接收该文件 Blob 后内部用一个回放 Client 逐帧重放桌面。
 * - 1x 走原生 play()（帧精确调度，最流畅）；
 * - 非 1x 无原生倍速，由本组件以虚拟时钟定时 seek() 步进实现；
 * - 进度条 seek() 可任意拖动（内部按关键帧快速重建画面）。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import Guacamole from 'guacamole-common-js'

const props = defineProps({
  sessionId: { type: String, required: true },
  url: { type: String, required: true },
})

const hostEl = ref(null)
const playing = ref(false)
const position = ref(0)
const duration = ref(0)
const speed = ref(1)
const loading = ref(false)
const error = ref('')

let recording, raf = 0, stepTimer = null
// 非 1x 步进状态
let stepping = false
let stepStartReal = 0
let stepStartPos = 0

function fmt(ms) {
  if (!isFinite(ms) || ms < 0) ms = 0
  const s = Math.floor(ms / 1000)
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

function stopStepping() {
  stepping = false
  if (stepTimer) { clearInterval(stepTimer); stepTimer = null }
}

function startStepping() {
  stopStepping()
  stepping = true
  stepStartReal = Date.now()
  stepStartPos = position.value
  // 约 8fps 推进 seek：桌面录像关键帧重建较重，过高频率反而卡顿
  stepTimer = setInterval(() => {
    if (!recording) return
    const target = stepStartPos + (Date.now() - stepStartReal) * speed.value
    if (target >= duration.value) {
      recording.seek(duration.value)
      setPlaying(false)
      stopStepping()
      return
    }
    recording.seek(target)
  }, 125)
}

function setPlaying(v) {
  playing.value = v
  if (!v) stopStepping()
}

function togglePlay() {
  if (!recording) return
  if (playing.value) {
    recording.pause()
    setPlaying(false)
    return
  }
  // 已到结尾则从头播放
  if (position.value >= duration.value && duration.value > 0) {
    recording.seek(0)
    position.value = 0
  }
  if (speed.value === 1) {
    recording.play()
    setPlaying(true)
  } else {
    startStepping()
    setPlaying(true)
  }
}

function restart() {
  if (!recording) return
  recording.seek(0, () => {
    position.value = 0
    if (speed.value === 1) { recording.play(); setPlaying(true) }
    else { stepStartPos = 0; stepStartReal = Date.now(); startStepping(); setPlaying(true) }
  })
}

function onSeek(e) {
  if (!recording) return
  const t = Number(e.target.value)
  recording.pause()
  stopStepping()
  playing.value = false
  recording.seek(t, () => { position.value = t })
}

function onSpeedChange() {
  // 切换倍速时保持播放/暂停状态一致
  if (!playing.value) return
  recording.pause()
  stopStepping()
  if (speed.value === 1) {
    recording.play()
    stepping = false
  } else {
    startStepping()
  }
}

onMounted(async () => {
  loading.value = true
  try {
    const resp = await fetch(props.url, {
      headers: { Authorization: `Bearer ${localStorage.getItem('token')}` },
    })
    if (resp.status === 409) throw new Error('会话仍在线，结束后才可回放')
    if (resp.status === 404) throw new Error('该会话无录屏文件或已被清理')
    if (!resp.ok) throw new Error(`录像加载失败（HTTP ${resp.status}）`)
    const blob = await resp.blob()

    recording = new Guacamole.SessionRecording(blob)
    const display = recording.getDisplay()
    hostEl.value.appendChild(display.getElement())
    display.onresize = (dw, dh) => {
      // 按容器等比缩放（不放大超过原始分辨率）
      const scale = Math.min(hostEl.value.clientWidth / dw,
                             hostEl.value.clientHeight / dh, 1)
      display.scale(scale)
    }
    recording.onplay = () => setPlaying(true)
    recording.onpause = () => setPlaying(false)
    recording.onerror = (msg) => { error.value = msg || '图形录像回放失败' }
    recording.onload = () => {
      loading.value = false
      duration.value = recording.getDuration()
      // 加载完成自动播放
      recording.play()
      setPlaying(true)
    }

    // rAF 仅用于刷新进度条/时长，渲染由 SessionRecording 内部驱动
    raf = requestAnimationFrame(function poll() {
      raf = requestAnimationFrame(poll)
      if (!recording) return
      position.value = recording.getPosition()
      duration.value = recording.getDuration()
      if (playing.value && speed.value === 1 &&
          position.value >= duration.value && duration.value > 0) {
        setPlaying(false)
      }
    })
  } catch (e) {
    error.value = e.message || String(e)
    loading.value = false
  }
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  stopStepping()
  try {
    recording?.pause()
    recording?.abort?.()
  } catch { /* ignore */ }
  if (hostEl.value) hostEl.value.innerHTML = ''
})

// 与 CastPlayer 保持一致：入参为相对录像起点的秒数，定位后自动播放
defineExpose({
  seekTo(sec) {
    if (!recording) return
    recording.pause()
    stopStepping()
    recording.seek(sec * 1000, () => {
      if (speed.value === 1) { recording.play(); setPlaying(true) }
      else { startStepping(); setPlaying(true) }
    })
  },
})
</script>

<style scoped>
.guac-player {
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  background: #000;
}
.guac-screen {
  height: 62vh; min-height: 360px; overflow: auto;
  display: flex; align-items: flex-start; justify-content: center;
  background: #000; padding: 8px;
}
.guac-controls {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 12px; background: var(--panel-2);
  border-top: 1px solid var(--border); font-size: 12px;
}
.pc-btn {
  background: transparent; border: 1px solid var(--border); color: var(--text);
  border-radius: 6px; width: 32px; height: 28px; cursor: pointer; font-size: 13px;
}
.pc-seek { flex: 1; padding: 0; }
.pc-time { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.pc-speed { width: auto; padding: 3px 6px; font-size: 12px; }
</style>
