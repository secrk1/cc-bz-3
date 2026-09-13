<template>
  <div class="cast-player">
    <div ref="hostEl" class="cast-screen"></div>
    <div class="cast-controls">
      <button class="pc-btn" :title="playing ? '暂停' : '播放'" @click="togglePlay">
        {{ playing ? '⏸' : '▶' }}
      </button>
      <button class="pc-btn" title="重新开始" @click="restart">⏮</button>
      <input class="pc-seek" type="range" min="0" :max="duration || 0"
             step="0.01" :value="virtualT" @input="onSeek">
      <span class="pc-time">{{ fmt(virtualT) }} / {{ fmt(duration) }}</span>
      <select v-model.number="speed" class="pc-speed" title="播放速度">
        <option :value="0.5">0.5x</option>
        <option :value="1">1x</option>
        <option :value="2">2x</option>
        <option :value="4">4x</option>
      </select>
      <label class="pc-idle">
        <input type="checkbox" v-model="skipIdle"> 跳过空闲
      </label>
    </div>
    <div v-if="loading" class="muted" style="padding:8px 2px">录像加载中…</div>
    <div v-if="error" class="error" style="padding:8px 2px">{{ error }}</div>
  </div>
</template>

<script setup>
/* 自包含 asciicast v2 播放器（基于已依赖的 xterm.js）。
 *
 * .cast 结构：首行 header JSON，其余每行 [time_offset, "o"|"i", data]。
 * 仅渲染 "o"（终端输出）事件，与 asciinema 官方播放器行为一致。
 * 调度：requestAnimationFrame 驱动虚拟时间轴，支持播放/暂停/拖动/倍速，
 * “跳过空闲”把长于 MAX_IDLE 的无输出间隔压缩到 MAX_IDLE。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Terminal } from 'xterm'
import 'xterm/css/xterm.css'

const props = defineProps({
  sessionId: { type: String, required: true },
  // 带 JWT 的录像地址由父级给出完整路径
  url: { type: String, required: true },
})

const MAX_IDLE = 2

const hostEl = ref(null)
const playing = ref(false)
const virtualT = ref(0)
const duration = ref(0)
const speed = ref(1)
const skipIdle = ref(true)
const loading = ref(false)
const error = ref('')

let term, events = [], pos = 0, raf = 0, lastWall = 0

function fmt(sec) {
  if (!isFinite(sec)) sec = 0
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function parseCast(text) {
  const lines = text.split('\n')
  const header = JSON.parse(lines[0])
  if (header.version !== 2) throw new Error('仅支持 asciicast v2 格式')
  const evs = []
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i].trim()
    if (!line) continue
    let row
    try { row = JSON.parse(line) } catch { continue }
    if (Array.isArray(row) && row.length === 3 && row[1] === 'o') {
      evs.push({ t: Number(row[0]) || 0, data: String(row[2]) })
    }
  }
  return { header, evs }
}

function writeDue() {
  while (pos < events.length && events[pos].t <= virtualT.value) {
    term.write(events[pos].data)
    pos++
  }
  if (pos >= events.length && playing.value) {
    playing.value = false
  }
}

function seekTo(sec, autoplay = false) {
  if (!events.length) return
  virtualT.value = Math.max(0, Math.min(sec, duration.value))
  // 二分找到第一个 t > 目标时间的事件，重放其之前全部输出
  let lo = 0, hi = events.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (events[mid].t <= virtualT.value) lo = mid + 1
    else hi = mid
  }
  pos = lo
  term.reset()
  if (pos > 0) {
    // 合并成一次 write，避免长会话定位时成千上万次入队
    term.write(events.slice(0, pos).map((e) => e.data).join(''))
  }
  if (autoplay) {
    playing.value = true
    lastWall = performance.now()
  }
}

function togglePlay() {
  if (!events.length) return
  if (pos >= events.length) seekTo(0)
  playing.value = !playing.value
  lastWall = performance.now()
}

function restart() { seekTo(0, true) }
function onSeek(e) { seekTo(Number(e.target.value)) }

async function load() {
  loading.value = true
  error.value = ''
  playing.value = false
  virtualT.value = 0
  events = []
  pos = 0
  try {
    const resp = await fetch(props.url, {
      headers: { Authorization: `Bearer ${localStorage.getItem('token')}` },
    })
    if (resp.status === 409) throw new Error('会话仍在线，结束后才可回放')
    if (resp.status === 404) throw new Error('该会话无录屏文件或已被清理')
    if (!resp.ok) throw new Error(`录像加载失败（HTTP ${resp.status}）`)
    const text = await resp.text()
    const { header, evs } = parseCast(text)
    if (!evs.length) throw new Error('录像文件中没有可播放的输出事件')
    events = evs
    duration.value = evs[evs.length - 1].t

    term = new Terminal({
      // 严格使用录像 header 中的列宽/行高，避免按容器缩放导致回放错位
      cols: Number(header.width) || 120,
      rows: Number(header.height) || 30,
      fontFamily: 'Menlo, Consolas, monospace',
      fontSize: 13,
      scrollback: 5000,
      theme: { background: '#0b1020' },
    })
    term.open(hostEl.value)
    // 加载完成自动开始回放
    playing.value = true
    lastWall = performance.now()
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    loading.value = false
  }
}

onMounted(load)

raf = requestAnimationFrame(function loop(now) {
  raf = requestAnimationFrame(loop)
  if (!playing.value) { lastWall = now; return }
  const dt = (now - lastWall) / 1000
  lastWall = now
  virtualT.value += dt * speed.value
  if (skipIdle.value && pos < events.length &&
      events[pos].t - virtualT.value > MAX_IDLE) {
    virtualT.value = events[pos].t - MAX_IDLE
  }
  if (pos >= events.length) {
    virtualT.value = duration.value
    playing.value = false
    return
  }
  writeDue()
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  term?.dispose()
})

defineExpose({ seekTo })
</script>

<style scoped>
.cast-player {
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  background: #0b1020;
}
.cast-screen { padding: 10px; min-height: 320px; max-height: 60vh; overflow: auto; }
.cast-controls {
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
.pc-idle { color: var(--muted); white-space: nowrap; display: flex; align-items: center; gap: 4px; }
.pc-idle input { width: auto; }
</style>
