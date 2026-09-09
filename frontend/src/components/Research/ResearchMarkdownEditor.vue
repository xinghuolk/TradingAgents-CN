<template>
  <div class="markdown-editor">
    <div class="editor-toolbar">
      <el-radio-group v-if="!readonly" v-model="mode" size="small" aria-label="正文视图">
        <el-radio-button label="edit">编辑</el-radio-button>
        <el-radio-button label="preview">预览</el-radio-button>
      </el-radio-group>
      <span v-else>正文</span>
      <div class="editor-tools">
        <el-button
          v-if="generationEntry?.status === 'draft' && !readonly"
          :icon="MagicStick"
          text
          size="small"
          :disabled="disabled"
          @click="generationVisible = true"
          >AI 生成草稿</el-button
        >
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
    <section
      v-for="draft in generationEntry?.ai_drafts || []"
      :key="draft.task_id"
      class="ai-original"
      aria-label="AI 原始草稿"
    >
      <div class="original-heading">
        <h3>AI 原始草稿</h3>
        <el-button v-if="!readonly" :icon="CopyDocument" :disabled="disabled" @click="adopt(draft)"
          >采用到正文</el-button
        >
      </div>
      <dl class="original-metadata">
        <dt>提供商</dt>
        <dd>{{ draft.provider }}</dd>
        <dt>模型</dt>
        <dd>{{ draft.model_name }}</dd>
        <dt>推理强度</dt>
        <dd>{{ draft.reasoning_effort || '默认（未单独设置）' }}</dd>
        <dt>生成时间</dt>
        <dd>{{ formatDateTime(draft.generated_at) }}</dd>
        <dt>提示词版本</dt>
        <dd>{{ draft.prompt_version }}</dd>
        <dt>任务 ID</dt>
        <dd>{{ draft.task_id }}</dd>
        <dt>来源 ID</dt>
        <dd>{{ draft.source_ids.join('、') || '无' }}</dd>
        <dt>引用资料</dt>
        <dd>
          <span v-if="!draft.references.length">无</span>
          <p v-for="(item, index) in draft.references" :key="index">{{ referenceLabel(item) }}</p>
        </dd>
      </dl>
      <article
        class="markdown-preview original-content"
        aria-label="AI 原始草稿正文"
        v-html="renderResearchMarkdown(draft.content)"
      />
    </section>
    <GenerationDialog
      v-if="generationVisible && generationEntry?.status === 'draft'"
      :entry="{
        ...generationEntry,
        references: generationReferences || generationEntry.references
      }"
      :before-submit="beforeGenerate"
      :initial-task="generationTask"
      @task="generationTask = $event"
      @close="generationVisible = false"
      @completed="generated"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { CopyDocument, MagicStick, RefreshRight } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import GenerationDialog from '@/components/Research/GenerationDialog.vue'
import { formatDateTime } from '@/utils/datetime'
import type {
  GenerationTask,
  ResearchAIDraft,
  ResearchEntry,
  ResearchReference
} from '@/api/stockResearch'
import type { ResearchSaveState } from '@/composables/useResearchAutosave'
import { renderResearchMarkdown } from '@/utils/researchMarkdown'

const props = withDefaults(
  defineProps<{
    modelValue: string
    state?: ResearchSaveState
    readonly?: boolean
    disabled?: boolean
    label?: string
    generationEntry?: ResearchEntry | null
    generationReferences?: ResearchReference[]
    beforeGenerate?: () => Promise<boolean>
  }>(),
  { state: 'saved', readonly: false, disabled: false, label: '正文' }
)
const emit = defineEmits<{
  'update:modelValue': [value: string]
  retry: []
  generated: [draft: ResearchAIDraft]
}>()
const mode = ref('edit')
const generationVisible = ref(false)
const generationTask = ref<GenerationTask | null>(null)
function generated(task: GenerationTask) {
  if (task.content === null) return
  emit('generated', {
    content: task.content,
    provider: task.provider,
    model_name: task.model_name,
    reasoning_effort: task.reasoning_effort,
    generated_at: task.generated_at,
    prompt_version: task.prompt_version,
    task_id: task.id,
    source_ids: [...task.source_ids],
    references: task.references.map(item => ({ ...item }))
  })
}
function referenceLabel(item: ResearchReference & { available?: boolean }) {
  const account =
    item.account_type === 'real' || item.kind === 'real_trade'
      ? '真实账户'
      : item.account_type === 'paper' || item.kind === 'paper_trade'
        ? '模拟账户'
        : item.kind === 'decision'
          ? '历史决策'
          : '分析报告'
  return `${account} · ${item.label || item.source_id} (${item.source_id})${item.available === false ? '（来源暂不可用）' : ''}`
}
async function adopt(draft: ResearchAIDraft) {
  if (props.readonly || props.disabled) return
  try {
    await ElMessageBox.confirm('用这份 AI 原始草稿替换当前正文？', '采用到正文', {
      confirmButtonText: '确认采用',
      cancelButtonText: '取消',
      type: 'warning'
    })
    if (props.readonly || props.disabled) return
    mode.value = 'edit'
    emit('update:modelValue', draft.content)
  } catch {
    /* Cancelling keeps the human text. */
  }
}
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
  min-height: 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  border-bottom: 1px solid var(--el-border-color-light);
}
.editor-tools {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}
.ai-original {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid var(--el-border-color);
}
.original-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
.original-heading h3 {
  font-size: 16px;
  margin: 0;
}
.original-metadata {
  display: grid;
  grid-template-columns: 90px minmax(0, 1fr);
  gap: 10px;
  font-size: 13px;
}
.original-metadata dt {
  color: var(--el-text-color-secondary);
}
.original-metadata dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.original-metadata p {
  margin: 0 0 6px;
}
.original-content {
  min-height: 0;
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
