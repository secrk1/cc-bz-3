<template>
  <AppLayout>
    <div class="toolbar">
      <h1 style="margin:0">文件传输记录</h1>
      <div class="filters">
        <select v-model="fDirection" @change="load">
          <option value="">全部方向</option>
          <option value="upload">上传</option>
          <option value="download">下载</option>
        </select>
        <select v-model="fStatus" @change="load">
          <option value="">全部状态</option>
          <option value="success">成功</option>
          <option value="uploading">进行中</option>
          <option value="failed">失败</option>
          <option value="aborted">已中止</option>
        </select>
        <button class="btn ghost" @click="load">刷新</button>
      </div>
    </div>

    <div class="card">
      <table>
        <thead>
          <tr>
            <th style="width:70px">方向</th>
            <th>文件名</th>
            <th>资产</th>
            <th>操作人</th>
            <th style="width:110px">大小</th>
            <th style="width:90px">状态</th>
            <th style="width:230px">MD5</th>
            <th style="width:170px">开始时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in filtered" :key="t.id">
            <td>
              <span class="badge" :class="t.direction">
                {{ t.direction === 'upload' ? '⬆ 上传' : '⬇ 下载' }}
              </span>
            </td>
            <td :title="t.remote_path">
              {{ t.filename }}
              <div v-if="t.error" class="row-err">{{ t.error }}</div>
            </td>
            <td>{{ t.asset_name }}</td>
            <td>{{ t.user_name }}</td>
            <td class="muted">{{ fmtSize(t.size) }}</td>
            <td><span class="badge" :class="t.status">{{ statusText(t.status) }}</span></td>
            <td><code class="md5">{{ t.md5 || '—' }}</code></td>
            <td class="muted">{{ fmt(t.started_at) }}</td>
          </tr>
          <tr v-if="!filtered.length"><td colspan="8" class="muted">暂无传输记录</td></tr>
        </tbody>
      </table>
    </div>
  </AppLayout>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import AppLayout from '../components/AppLayout.vue'
import api from '../api'

const rows = ref([])
const fDirection = ref('')
const fStatus = ref('')

const filtered = computed(() => rows.value.filter((t) =>
  (!fDirection.value || t.direction === fDirection.value) &&
  (!fStatus.value || t.status === fStatus.value)))

function fmtSize(n) {
  if (!n) return '0 B'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let v = n, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${units[i]}`
}
function fmt(t) { return new Date(t).toLocaleString('zh-CN', { hour12: false }) }
function statusText(s) {
  return { success: '成功', uploading: '进行中', failed: '失败',
           aborted: '已中止' }[s] || s
}

async function load() {
  const { data } = await api.get('/api/transfers')
  rows.value = data
}
onMounted(load)
</script>

<style scoped>
.filters { display: flex; gap: 10px; }
.filters select { width: 120px; }
.row-err { color: var(--danger); font-size: 12px; margin-top: 2px;
           max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.md5 { font-size: 11px; color: var(--muted); }
.badge.upload { color: #7dd3fc; border-color: #0369a1; }
.badge.download { color: #f0abfc; border-color: #a21caf; }
.badge.success { color: var(--ok); border-color: #065f46; }
.badge.uploading { color: #fcd34d; border-color: #92400e; }
.badge.failed, .badge.aborted { color: var(--danger); border-color: #7f1d1d; }
</style>
