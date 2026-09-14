<template>
  <div>
    <div class="tree-row" :class="{ active: selected === node.path }"
         :style="{ paddingLeft: depth * 14 + 6 + 'px' }"
         @click="onClick">
      <span class="twist" :class="{ open: node.expanded }">
        {{ node.loading ? '…' : (node.expanded ? '▾' : '▸') }}
      </span>
      <span class="dir-icon">📁</span>
      <span class="dir-name">{{ node.name }}</span>
    </div>
    <div v-if="node.expanded">
      <DirTreeNode v-for="child in node.children" :key="child.path"
                   :node="child" :asset-id="assetId"
                   :selected="selected" :depth="depth + 1"
                   @toggle="$emit('toggle', $event)"
                   @select="$emit('select', $event)" />
    </div>
  </div>
</template>

<script>
export default {
  name: 'DirTreeNode',
  props: {
    node: { type: Object, required: true },
    assetId: { type: [String, Number], required: true },
    selected: { type: String, default: '' },
    depth: { type: Number, default: 0 },
  },
  emits: ['toggle', 'select'],
  methods: {
    onClick() {
      // 展开/折叠由父级 DirTree 异步加载子目录，同时切换右侧列表
      this.$emit('toggle', this.node)
    },
  },
}
</script>

<style scoped>
.tree-row {
  display: flex; align-items: center; gap: 4px;
  padding: 4px 6px; border-radius: 6px; cursor: pointer;
  white-space: nowrap; user-select: none;
}
.tree-row:hover { background: var(--panel-2); }
.tree-row.active { background: rgba(56, 189, 248, .16); color: var(--accent); }
.twist { width: 12px; color: var(--muted); display: inline-block; }
.dir-icon { font-size: 13px; }
.dir-name { overflow: hidden; text-overflow: ellipsis; }
</style>
