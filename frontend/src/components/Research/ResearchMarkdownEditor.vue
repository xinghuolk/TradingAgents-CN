<template>
  <div class="markdown-editor">
    <div class="editor-toolbar">
      <el-radio-group v-if="!readonly" v-model="mode" size="small" aria-label="正文视图">
        <el-radio-button label="edit">编辑</el-radio-button>
        <el-radio-button label="preview">预览</el-radio-button>
      </el-radio-group>
      <span v-else>正文</span>
      <div v-if="!readonly" class="save-status" role="status" aria-live="polite">
        <span :class="{ failed: state === 'failed' }">{{ stateLabel[state] }}</span>
        <el-button
          v-if="state === 'failed'"
          text
          size="small"
          :disabled="disabled"
          @click="$emit('retry')"
        >
          <el-icon><RefreshRight /></el-icon>重试
        </el-button>
      </div>
    </div>
    <textarea
      v-if="mode === 'edit' && !readonly"
      class="markdown-input"
      :value="modelValue"
      :disabled="disabled"
      :aria-label="label"
      spellcheck="false"
      @input="$emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
    />
    <!-- The dedicated renderer escapes raw HTML and restricts link/image protocols. -->
    <article v-else class="markdown-preview" :aria-label="label" v-html="preview" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { RefreshRight } from '@element-plus/icons-vue'
import type { ResearchSaveState } from '@/composables/useResearchAutosave'
import { renderResearchMarkdown } from '@/utils/researchMarkdown'

const props = withDefaults(
  defineProps<{
    modelValue: string
    state?: ResearchSaveState
    readonly?: boolean
    disabled?: boolean
    label?: string
  }>(),
  { state: 'saved', readonly: false, disabled: false, label: '正文' }
)
defineEmits<{ 'update:modelValue': [value: string]; retry: [] }>()
const mode = ref('edit')
const preview = computed(() => renderResearchMarkdown(props.modelValue))
const stateLabel: Record<ResearchSaveState, string> = {
  saved: '已保存',
  dirty: '未保存',
  saving: '保存中',
  failed: '保存失败'
}
</script>

<style scoped>
.markdown-editor {
  min-width: 0;
  width: 100%;
}
.editor-toolbar {
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border-bottom: 1px solid var(--el-border-color-light);
}
.save-status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}
.failed {
  color: var(--el-color-danger);
}
.markdown-input,
.markdown-preview {
  box-sizing: border-box;
  width: 100%;
  min-height: 360px;
  margin: 0;
  padding: 16px 4px;
  color: var(--el-text-color-primary);
  background: transparent;
  font-size: 14px;
  line-height: 1.8;
  overflow-wrap: anywhere;
}
.markdown-input {
  display: block;
  border: 0;
  resize: vertical;
  font-family: ui-monospace, monospace;
}
.markdown-input:focus-visible {
  outline: 2px solid var(--el-color-primary-light-5);
  outline-offset: 2px;
}
.markdown-preview :deep(img) {
  max-width: 100%;
  height: auto;
}
.markdown-preview :deep(pre) {
  overflow-x: auto;
  padding: 12px;
  background: var(--el-fill-color-light);
}
.markdown-preview :deep(table) {
  display: block;
  max-width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
}
.markdown-preview :deep(td),
.markdown-preview :deep(th) {
  padding: 6px 10px;
  border: 1px solid var(--el-border-color-light);
}
.markdown-preview :deep(h1) {
  font-size: 22px;
}
.markdown-preview :deep(h2) {
  font-size: 19px;
}
.markdown-preview :deep(h3) {
  font-size: 16px;
}
.markdown-preview :deep(a) {
  color: var(--el-color-primary);
}
.markdown-preview :deep(blockquote) {
  margin-left: 0;
  padding-left: 16px;
  border-left: 3px solid var(--el-border-color);
}
</style>
