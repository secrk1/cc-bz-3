<template>
  <AppLayout>
    <div class="toolbar">
      <h1 style="margin:0">📁 文件管理 · {{ asset ? asset.name : '加载中…' }}</h1>
      <button class="btn ghost" @click="$router.push('/')">返回资产</button>
    </div>

    <div v-if="error" class="card error-banner">{{ error }}</div>

    <div class="fm-layout">
      <aside class="fm-tree card">
        <div class="tree-title">目录</div>
        <DirTree ref="treeRef" :asset-id="assetId" :selected="cwd"
                 @select="navigate" />
      </aside>

      <section class="fm-main card">
        <div class="breadcrumb">
          <template v-for="(seg, i) in crumbs" :key="seg.path">
            <a v-if="i < crumbs.length - 1" @click="navigate(seg.path)">{{ seg.name }}</a>
            <span v-else class="crumb-cur">{{ seg.name }}</span>
            <span v-if="i < crumbs.length - 1" class="sep">/</span>
          </template>
        </div>

        <div class="fm-tools">
          <button class="btn" :disabled="loading" @click="pickUpload">⬆ 上传到当前目录</button>
          <input ref="fileInput" type="file" multiple hidden @change="onPick" />
          <button class="btn ghost" :disabled="loading" @click="load(cwd)">刷新</button>
          <span class="muted">单用户最多 2 个文件同时传输；大文件自动分片、可续传</span>
        </div>

        <table>
          <thead>
            <tr>
              <th>名称</th><th style="width:110px">大小</th>
              <th style="width:180px">修改时间</th><th style="width:170px"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in entries" :key="e.name"
                @dblclick="e.is_dir && navigate(join(cwd, e.name))">
              <td>
                <span v-if="e.is_dir" class="fm-name dir"
                      @click="navigate(join(cwd, e.name))">📁 {{ e.name }}</span>
                <span v-else class="fm-name">📄 {{ e.name }}</span>
              </td>
              <td class="muted">{{ e.is_dir ? '-' : fmtSize(e.size) }}</td>
              <td class="muted">{{ e.mtime ? fmtTime(e.mtime) : '-' }}</td>
              <td>
                <button v-if="!e.is_dir" class="btn ghost btn-sm"
                        @click="openEditor(e)">编辑</button>
                <button v-if="!e.is_dir" class="btn ghost btn-sm"
                        style="margin-left:6px" @click="download(e)">下载</button>
              </td>
            </tr>
            <tr v-if="!entries.length && !loading">
              <td colspan="4" class="muted">目录为空</td>
            </tr>
          </tbody>
        </table>

        <div v-if="jobs.length" class="jobs">
          <div class="jobs-head">
            <strong>传输队列</strong>
            <button class="btn ghost btn-sm" @click="clearJobs">清除已完成</button>
          </div>
          <div v-for="j in jobs" :key="j.id" class="job">
            <div class="job-line">
              <span class="job-name">{{ j.kind === 'download' ? '📥' : '📤' }} {{ j.name }}</span>
              <span class="job-meta">{{ fmtSize(j.loaded) }} / {{ fmtSize(j.size) }}</span>
              <span class="job-status" :class="j.status">{{ statusText(j) }}</span>
              <span class="job-meta" v-if="j.speed > 0">{{ fmtSize(j.speed) }}/s</span>
              <button v-if="j.status === 'active' || j.status === 'waiting'"
                      class="btn danger btn-sm" @click="cancelJob(j)">取消</button>
              <button v-if="j.status === 'failed'" class="btn btn-sm"
                      @click="retryJob(j)">重试</button>
            </div>
            <div class="bar"><div class="bar-fill" :style="{ width: pct(j) + '%' }"></div></div>
            <div v-if="j.error" class="job-err">{{ j.error }}</div>
          </div>
        </div>
      </section>
    </div>

    <dialog ref="editorDialog" class="editor-dialog" @close="editing = null">
      <form method="dialog" @submit.prevent="save">
        <h2 style="color:var(--text)">在线编辑</h2>
        <p class="muted editor-path">{{ editing?.path }}</p>
        <textarea v-model="editContent" class="editor" spellcheck="false"></textarea>
        <p v-if="editError" class="error">{{ editError }}</p>
        <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:12px">
          <button type="button" class="btn ghost" @click="closeEditor">取消</button>
          <button class="btn" :disabled="saving">{{ saving ? '保存中…' : '保存' }}</button>
        </div>
      </form>
    </dialog>
  </AppLayout>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import DirTree from '../components/DirTree.vue'
import api from '../api'
import {
  jobs, enqueueUpload, enqueueDownload, cancelJob, retryJob, clearFinished,
} from '../uploader'

const props = defineProps({ id: { type: [String, Number], required: true } })
const assetId = computed(() => props.id)

const asset = ref(null)
const cwd = ref('/')
const entries = ref([])
const loading = ref(false)
const error = ref('')
const treeRef = ref(null)
const fileInput = ref(null)

const editing = ref(null)
const editContent = ref('')
const editError = ref('')
const saving = ref(false)
const editorDialog = ref(null)

function join(base, name) {
  return base === '/' ? `/${name}` : `${base}/${name}`
}

const crumbs = computed(() => {
  const parts = cwd.value.split('/').filter(Boolean)
  const list = [{ name: '/', path: '/' }]
  let acc = ''
  for (const p of parts) {
    acc += `/${p}`
    list.push({ name: p, path: acc })
  }
  return list
})

function fmtSize(n) {
  if (n < 1024) return `${n} B`
  const units = ['KiB', 'MiB', 'GiB']
  let v = n / 1024, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${units[i]}`
}
function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

async function load(path) {
  loading.value = true
  error.value = ''
  try {
    const { data } = await api.get(`/api/sftp/${props.id}/list`,
                                   { params: { path } })
    cwd.value = data.path
    entries.value = data.entries
  } catch (e) {
    error.value = e.response?.data?.detail || '读取目录失败'
  } finally {
    loading.value = false
  }
}
const navigate = load

async function pickUpload() {
  fileInput.value.click()
}

function onPick(e) {
  const files = Array.from(e.target.files || [])
  e.target.value = ''
  for (const f of files) enqueueUpload(props.id, f, cwd.value)
}

function pct(j) {
  return j.size ? Math.min(100, Math.round((j.loaded / j.size) * 100)) : 0
}
function statusText(j) {
  const map = { waiting: '排队中', active: j.kind === 'download' ? '下载中' : '上传中',
                success: '已完成', failed: '失败', canceled: '已取消' }
  return map[j.status] || j.status
}
function clearJobs() { clearFinished() }

// 任务首次进入 success 时自动刷新当前目录（按 id 去重，避免进度更新反复刷新）
const reloaded = new Set()
watch(jobs, (list) => {
  for (const j of list) {
    if (j.status === 'success' && !reloaded.has(j.id)) {
      reloaded.add(j.id)
      load(cwd.value)
    }
  }
}, { deep: true })

async function openEditor(file) {
  const path = join(cwd.value, file.name)
  editError.value = ''
  editContent.value = ''
  try {
    const { data } = await api.get(`/api/sftp/${props.id}/content`,
                                   { params: { path } })
    editing.value = { name: file.name, path: data.path }
    editContent.value = data.content
    // 用 showModal() 弹出真正的模态框（居中遮罩、Esc/点遮罩可关）
    editorDialog.value?.showModal()
  } catch (e) {
    alert(e.response?.data?.detail || '无法打开该文件')
  }
}

function closeEditor() {
  editorDialog.value?.close()
}

async function save() {
  saving.value = true
  editError.value = ''
  try {
    await api.post(`/api/sftp/${props.id}/content`,
                   { path: editing.value.path, content: editContent.value })
    closeEditor()
    await load(cwd.value)
  } catch (e) {
    editError.value = e.response?.data?.detail || '保存失败'
  } finally {
    saving.value = false
  }
}

async function download(file) {
  enqueueDownload(props.id, join(cwd.value, file.name), file.name, file.size)
}

onMounted(async () => {
  const list = await api.get('/api/assets')
  asset.value = list.data.find((x) => String(x.id) === String(props.id))
  // 初始目录列表由 DirTree 加载完成后 @select 触发 navigate
})
</script>

<style scoped>
.fm-layout { display: grid; grid-template-columns: 250px 1fr; gap: 16px; }
.fm-tree { max-height: calc(100vh - 170px); overflow: auto; padding: 12px; }
.tree-title { font-size: 12px; color: var(--muted); text-transform: uppercase;
              margin-bottom: 8px; }
.fm-main { padding: 16px 18px; }
.breadcrumb { font-size: 13px; margin-bottom: 12px; word-break: break-all; }
.breadcrumb a { cursor: pointer; }
.breadcrumb .sep { color: var(--muted); margin: 0 5px; }
.crumb-cur { color: var(--text); }
.fm-tools { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
.fm-name { cursor: default; }
.fm-name.dir { color: var(--accent); cursor: pointer; }
.btn-sm { padding: 4px 9px; font-size: 12px; }
.error-banner { color: var(--danger); margin-bottom: 14px; }
.editor-dialog {
  /* 覆盖全局 dialog 的固定 440px，自适应且不超出视口 */
  width: min(880px, 92vw);
  max-width: 92vw;
}
.editor-path { word-break: break-all; margin: 6px 0 12px; }
.jobs { margin-top: 18px; border-top: 1px solid var(--border); padding-top: 12px; }
.jobs-head { display: flex; justify-content: space-between; align-items: center;
             margin-bottom: 10px; font-size: 13px; }
.job { margin-bottom: 10px; }
.job-line { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.job-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.job-meta { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.job-status { white-space: nowrap; }
.job-status.active { color: var(--accent); }
.job-status.success { color: var(--ok); }
.job-status.failed, .job-status.canceled { color: var(--danger); }
.job-err { color: var(--danger); font-size: 12px; margin-top: 3px; }
.bar { height: 6px; background: var(--bg); border-radius: 4px; overflow: hidden;
       margin-top: 4px; }
.bar-fill { height: 100%; background: var(--accent-2); transition: width .2s; }
.editor {
  display: block;
  width: 100%;
  box-sizing: border-box;
  height: 56vh;
  max-height: 70vh;
  background: var(--bg); color: var(--text);
  border: 1px solid var(--border); border-radius: 8px;
  padding: 10px; font-family: Menlo, Consolas, monospace;
  font-size: 13px; resize: vertical;
}
</style>
