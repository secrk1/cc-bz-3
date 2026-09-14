/* 大文件分片上传管理器。
 *
 * - 按 CHUNK_SIZE 切片，顺序 PUT 原始字节分片（X-Chunk-Start 声明偏移）；
 * - 全局最多 MAX_CONCURRENT 个文件同时上传（与服务端每用户 2 槽对齐，
 *   其余本地排队，避免无谓的 429）；
 * - 分片返回 409（偏移不连续）时按响应头 X-Received 重新定位，实现续传；
 * - 每个任务暴露状态/已传字节/速率供进度条渲染；
 * - 取消调用 abort 释放服务端并发槽并清理临时分片。
 */
import { reactive, ref } from 'vue'
import api from './api'

const CHUNK_SIZE = 8 * 1024 * 1024
const MAX_CONCURRENT = 2

export const jobs = ref([])

let active = 0
const waiting = []

export function enqueueUpload(assetId, file, remoteDir) {
  const job = reactive({
    id: `up-${file.name}-${file.size}-${Math.random().toString(36).slice(2, 8)}`,
    kind: 'upload',
    assetId,
    remoteDir,
    name: file.name,
    size: file.size,
    loaded: 0,
    speed: 0,
    // waiting / active / success / failed / canceled
    status: 'waiting',
    error: '',
    transferId: '',
    _file: file,
    _abort: null,
  })
  jobs.value.unshift(job)
  waiting.push(job)
  pump()
  return job
}

export function enqueueDownload(assetId, path, filename, size) {
  const job = reactive({
    id: `dl-${filename}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    kind: 'download',
    assetId,
    remotePath: path,
    name: filename,
    size: size || 0,
    loaded: 0,
    speed: 0,
    status: 'waiting',
    error: '',
    transferId: '',
    _abort: null,
  })
  jobs.value.unshift(job)
  waiting.push(job)
  pump()
  return job
}

function pump() {
  while (active < MAX_CONCURRENT && waiting.length) {
    const job = waiting.shift()
    active++
    const runner = job.kind === 'download' ? runDownload : runJob
    runner(job).finally(() => { active--; pump() })
  }
}

async function runDownload(job) {
  job.status = 'active'
  job._abort = new AbortController()
  const url = `/api/sftp/${job.assetId}/download-audit?path=${encodeURIComponent(job.remotePath)}`
  try {
    const token = localStorage.getItem('token')
    const resp = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
      signal: job._abort.signal,
    })
    if (!resp.ok) {
      const detail = await resp.json().catch(() => ({}))
      throw new Error(detail.detail || `HTTP ${resp.status}`)
    }
    const total = Number(resp.headers.get('content-length')) || job.size
    if (total) job.size = total
    const reader = resp.body.getReader()
    const chunks = []
    let received = 0
    let lastTick = Date.now()
    let lastLoaded = 0
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      chunks.push(value)
      received += value.length
      job.loaded = received
      const now = Date.now()
      if (now - lastTick >= 500) {
        job.speed = ((received - lastLoaded) * 1000) / (now - lastTick)
        lastTick = now; lastLoaded = received
      }
    }
    const blob = new Blob(chunks)
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = job.name
    link.click()
    URL.revokeObjectURL(link.href)
    job.speed = 0
    job.status = 'success'
  } catch (e) {
    if (e.name === 'AbortError' || job.status === 'canceled') {
      job.status = 'canceled'
      return
    }
    job.status = 'failed'
    job.speed = 0
    job.error = e.message || '下载失败'
  }
}

async function putChunk(job, base, start, end) {
  // 发送半开区间 [start, end)；409 且服务端已收到本片内的某个偏移时，
  // 从该偏移重发剩余部分，最多重试 5 次。返回服务端权威已收字节数。
  let off = start
  for (let attempt = 0; attempt < 5; attempt++) {
    const blob = job._file.slice(off, end)
    try {
      const resp = await api.put(`${base}/${job.transferId}/chunk`, blob, {
        headers: {
          'Content-Type': 'application/octet-stream',
          'X-Chunk-Start': String(off),
        },
        signal: job._abort.signal,
      })
      return resp.data.received
    } catch (e) {
      if (e.code === 'ERR_CANCELED' || job.status === 'canceled') {
        const c = new Error('canceled'); c.canceled = true; throw c
      }
      const received = Number(e.response?.headers?.['x-received'])
      if (e.response?.status === 409 && Number.isFinite(received)) {
        if (received >= end) return received          // 本片已被服务端记录
        if (off < received) { off = received; continue } // 重发本片剩余部分
      }
      throw e
    }
  }
  throw new Error('分片重传失败')
}

async function runJob(job) {
  job.status = 'active'
  job.error = ''
  const base = `/api/sftp/${job.assetId}/transfers`
  try {
    const init = await api.post(`${base}/init`, {
      filename: job.name, path: job.remoteDir, size: job.size,
    })
    job.transferId = init.data.transfer_id
    const chunkSize = init.data.chunk_size || CHUNK_SIZE

    // 查询服务端已收偏移（同任务恢复）
    let offset = 0
    try {
      const st = await api.get(`${base}/${job.transferId}`)
      offset = st.data.received || 0
    } catch { /* 新任务 */ }
    job.loaded = offset

    job._abort = new AbortController()
    let lastTick = Date.now()
    let lastLoaded = offset

    while (offset < job.size) {
      if (job.status === 'canceled') {
        const c = new Error('canceled'); c.canceled = true; throw c
      }
      const end = Math.min(offset + chunkSize, job.size)
      offset = await putChunk(job, base, offset, end)
      job.loaded = offset
      const now = Date.now()
      if (now - lastTick >= 500) {
        job.speed = ((job.loaded - lastLoaded) * 1000) / (now - lastTick)
        lastTick = now
        lastLoaded = job.loaded
      }
    }
    job.speed = 0

    await api.post(`${base}/${job.transferId}/complete`)
    job.loaded = job.size
    job.status = 'success'
  } catch (e) {
    if (e.canceled || job.status === 'canceled') {
      job.status = 'canceled'
      return
    }
    job.status = 'failed'
    job.speed = 0
    job.error = e.response?.data?.detail || e.message || '上传失败'
    // 失败即释放服务端并发槽（临时分片由 abort 清理）
    if (job.transferId) {
      try {
        await api.post(`${base}/${job.transferId}/abort`)
      } catch { /* 忽略 */ }
    }
  }
}

export async function cancelJob(job) {
  job.status = 'canceled'
  job._abort?.abort()
  if (job.kind === 'upload' && job.transferId) {
    try {
      await api.post(
        `/api/sftp/${job.assetId}/transfers/${job.transferId}/abort`)
    } catch { /* 忽略清理失败 */ }
  }
}

export function retryJob(job) {
  if (job.status !== 'failed') return
  job.status = 'waiting'
  job.loaded = 0
  job.speed = 0
  job.error = ''
  job.transferId = ''
  waiting.push(job)
  pump()
}

export function clearFinished() {
  jobs.value = jobs.value.filter(
    (j) => j.status === 'active' || j.status === 'waiting')
}
