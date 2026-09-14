<template>
  <div class="tree">
    <DirTreeNode v-if="root" :node="root" :asset-id="assetId"
                 :selected="selected" :depth="0"
                 @toggle="onToggle" />
    <div v-else-if="loading" class="muted tree-hint">加载目录…</div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import api from '../api'
import DirTreeNode from './DirTreeNode.vue'

const props = defineProps({
  assetId: { type: [String, Number], required: true },
  selected: { type: String, default: '' },
})
const emit = defineEmits(['select'])
const root = ref(null)
const loading = ref(false)

function join(base, name) {
  return base === '/' ? `/${name}` : `${base}/${name}`
}

function toNodes(data) {
  return data.entries
    .filter((e) => e.is_dir)
    .map((e) => ({
      name: e.name, path: join(data.path, e.name),
      children: [], loaded: false, expanded: false, loading: false,
    }))
}

async function onToggle(node) {
  node.expanded = !node.expanded
  if (node.expanded && !node.loaded) {
    node.loading = true
    try {
      const { data } = await api.get(`/api/sftp/${props.assetId}/list`,
                                     { params: { path: node.path } })
      node.children = toNodes(data)
      node.loaded = true
    } finally {
      node.loading = false
    }
  }
  emit('select', node.path)
}

onMounted(async () => {
  loading.value = true
  try {
    const { data } = await api.get(`/api/sftp/${props.assetId}/list`)
    root.value = {
      name: data.path === '/' ? '/' : `主目录（${data.path}）`,
      path: data.path,
      children: toNodes(data),
      loaded: true, expanded: true, loading: false,
    }
    emit('select', data.path)
  } finally {
    loading.value = false
  }
})

defineExpose({
  // 供外部刷新某节点（如上传后），简单起见重新加载当前根
  async reload() {
    const { data } = await api.get(`/api/sftp/${props.assetId}/list`)
    if (root.value) root.value.children = toNodes(data)
  },
})
</script>

<style scoped>
.tree { font-size: 13px; }
.tree-hint { padding: 8px 4px; }
</style>
