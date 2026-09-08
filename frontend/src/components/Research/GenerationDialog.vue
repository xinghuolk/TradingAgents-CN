<template>
  <el-dialog
    :model-value="true"
    title="AI 生成草稿"
    width="min(680px, 94vw)"
    append-to-body
    :close-on-click-modal="false"
    :show-close="!submitting"
    :close-on-press-escape="!submitting"
    @update:model-value="close"
  >
    <el-form label-position="top" :disabled="submitting || activeTask">
      <el-form-item label="提供商">
        <el-select
          v-model="provider"
          aria-label="提供商"
          :loading="loading"
          @change="changeProvider"
        >
          <el-option
            v-if="provider && !providers.includes(provider)"
            :value="provider"
            :label="provider"
            disabled
          />
          <el-option v-for="item in providers" :key="item" :value="item" :label="item" />
        </el-select>
      </el-form-item>
      <el-form-item label="模型">
        <el-select v-model="modelName" aria-label="模型" @change="changeModel">
          <el-option
            v-if="modelName && !selectedModel"
            :value="modelName"
            :label="`${modelName}（不可用）`"
            disabled
          />
          <el-option
            v-for="item in providerModels"
            :key="item.model_name"
            :value="item.model_name"
            :label="
              item.model_display_name
                ? `${item.model_display_name} (${item.model_name})`
                : item.model_name
            "
          />
        </el-select>
      </el-form-item>
      <el-form-item label="推理强度">
        <el-select
          v-model="effort"
          aria-label="推理强度"
          :disabled="submitting || activeTask || (!effortSupported && !effort)"
          :placeholder="effortSupported ? '默认' : '当前模型不支持单独设置'"
        >
          <el-option value="" :label="effortSupported ? '默认' : '当前模型不支持单独设置'" />
          <el-option
            v-if="effort && !effortOptions.some(item => item.value === effort)"
            :value="effort"
            :label="`${effort}（不再支持）`"
            disabled
          />
          <el-option
            v-for="item in effortOptions"
            :key="item.value"
            :value="item.value"
            :label="item.label"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="生成引用资料">
        <ResearchReferencePicker
          v-model="references"
          :security-id="entry.security_id"
          :security-ids="entry.security_ids"
          :decision-id="entry.decision_id || undefined"
          :disabled="submitting || activeTask"
        />
      </el-form-item>
    </el-form>
    <p v-if="selectionMessage" class="generation-message" role="status">{{ selectionMessage }}</p>
    <p v-if="selectionError" class="generation-error" role="alert">{{ selectionError }}</p>
    <div v-if="loadFailed" role="alert">
      模型配置加载失败
      <el-button text :icon="RefreshRight" @click="loadModels">重试模型配置</el-button>
    </div>
    <div v-if="task" class="task-state" role="status">
      <span>{{ statusLabels[task.status] }}</span>
      <small>任务 ID：{{ task.id }}</small>
    </div>
    <p v-if="errorMessage || task?.error_message" class="generation-error" role="alert">
      {{ errorMessage || task?.error_message }}
    </p>
    <template #footer>
      <div class="generation-actions">
        <el-button :disabled="submitting" @click="close">关闭</el-button>
        <el-button v-if="pollPaused && activeTask" :icon="RefreshRight" @click="resumePolling"
          >继续查询</el-button
        >
        <el-button
          v-if="!activeTask"
          type="primary"
          :icon="MagicStick"
          :disabled="
            !selectedModel || !!selectionError || loading || loadFailed || entry.status !== 'draft'
          "
          :loading="submitting"
          @click="submit"
          >{{ task?.status === 'failed' || submitFailed ? '重新生成' : '生成草稿' }}</el-button
        >
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { MagicStick, RefreshRight } from '@element-plus/icons-vue'
import { configApi, type LLMConfig } from '@/api/config'
import {
  stockResearchApi,
  type GenerationTask,
  type ResearchEntry,
  type ResearchReference
} from '@/api/stockResearch'
import ResearchReferencePicker from '@/components/Research/ResearchReferencePicker.vue'

const props = defineProps<{
  entry: ResearchEntry
  beforeSubmit?: () => Promise<boolean>
  initialTask?: GenerationTask | null
}>()
const emit = defineEmits<{
  close: []
  completed: [task: GenerationTask]
  task: [task: GenerationTask]
}>()
const models = ref<LLMConfig[]>([])
const provider = ref(props.initialTask?.provider || '')
const modelName = ref(props.initialTask?.model_name || '')
const effort = ref(props.initialTask?.reasoning_effort || '')
const references = ref<ResearchReference[]>(
  (props.initialTask ? props.initialTask.references : props.entry.references).map(item => ({
    ...item
  }))
)
const loading = ref(false)
const loadFailed = ref(false)
const submitting = ref(false)
const submitFailed = ref(false)
const selectionMessage = ref('')
const errorMessage = ref('')
const task = ref<GenerationTask | null>(props.initialTask || null)
const pollPaused = ref(false)
const activeTask = computed(
  () => task.value?.status === 'pending' || task.value?.status === 'running'
)
const providers = computed(() => [...new Set(models.value.map(item => item.provider))])
const providerModels = computed(() => models.value.filter(item => item.provider === provider.value))
const selectedModel = computed(() =>
  providerModels.value.find(item => item.model_name === modelName.value)
)
const effortSupported = computed(
  () =>
    !!selectedModel.value?.features?.includes('reasoning') &&
    !['codex', 'claude_code', 'anthropic', 'google', 'deepseek'].includes(provider.value)
)
const effortOptions = computed(() =>
  effortSupported.value
    ? [
        { value: 'low', label: '低' },
        { value: 'medium', label: '中' },
        { value: 'high', label: '高' }
      ]
    : []
)
const selectionError = computed(() => {
  if (loading.value || loadFailed.value) return ''
  if (modelName.value && !selectedModel.value) return '所选模型已不可用，请重新选择模型'
  if (effort.value && !effortOptions.value.some(item => item.value === effort.value))
    return '所选推理强度已不受当前模型支持，请重新选择推理强度'
  return ''
})
const statusLabels = {
  pending: '等待生成',
  running: '正在生成',
  completed: '生成完成',
  failed: '生成失败'
}
let mounted = true
let timer: ReturnType<typeof setTimeout> | undefined
let pollCount = 0
let polling = false
const maxPolls = 150
function preferenceKey() {
  try {
    const user = JSON.parse(localStorage.getItem('user-info') || '{}')
    return `research-generation:${user.id || user.username || 'local'}`
  } catch {
    return 'research-generation:local'
  }
}
async function loadModels() {
  if (loading.value) return
  loading.value = true
  loadFailed.value = false
  try {
    const configured = await configApi.getLLMConfigs()
    if (!mounted) return
    models.value = configured.filter(item => item.enabled)
    if (props.initialTask) return
    let saved: { provider?: string; model_name?: string; reasoning_effort?: string } = {}
    try {
      saved = JSON.parse(localStorage.getItem(preferenceKey()) || '{}')
    } catch {
      /* Optional local preference. */
    }
    if (
      models.value.some(
        item => item.provider === saved.provider && item.model_name === saved.model_name
      )
    ) {
      provider.value = saved.provider!
      modelName.value = saved.model_name!
      effort.value =
        effortSupported.value && ['low', 'medium', 'high'].includes(saved.reasoning_effort || '')
          ? saved.reasoning_effort!
          : ''
    } else if (saved.model_name) selectionMessage.value = '上次选择的模型已不可用，请重新选择'
    else if (!models.value.length) selectionMessage.value = '暂无已启用的模型'
  } catch {
    if (mounted) loadFailed.value = true
  } finally {
    if (mounted) loading.value = false
  }
}
function changeProvider() {
  modelName.value = ''
  effort.value = ''
  selectionMessage.value = ''
}
function changeModel() {
  effort.value = ''
  selectionMessage.value = ''
}
function stopPolling() {
  if (timer !== undefined) clearTimeout(timer)
  timer = undefined
}
function close() {
  mounted = false
  stopPolling()
  emit('close')
}
function receive(value: GenerationTask) {
  task.value = value
  emit('task', value)
  if (value.status === 'completed') {
    stopPolling()
    emit('completed', value)
    close()
  } else if (value.status === 'failed') stopPolling()
  else schedulePoll()
}
function schedulePoll() {
  stopPolling()
  if (!mounted || !activeTask.value) return
  if (pollCount >= maxPolls) {
    pollPaused.value = true
    errorMessage.value = '查询已暂停，任务仍在处理中'
    return
  }
  timer = setTimeout(poll, 2000)
}
async function poll() {
  timer = undefined
  if (!mounted || !activeTask.value || !task.value || polling) return
  polling = true
  pollCount += 1
  try {
    const response = await stockResearchApi.getGenerationTask(task.value.id)
    if (mounted) receive(response.data)
  } catch {
    if (mounted) {
      pollPaused.value = true
      errorMessage.value = '任务查询失败，请继续查询'
    }
  } finally {
    polling = false
  }
}
function resumePolling() {
  if (polling || !mounted || !pollPaused.value) return
  pollCount = 0
  pollPaused.value = false
  errorMessage.value = ''
  schedulePoll()
}
async function submit() {
  if (
    submitting.value ||
    activeTask.value ||
    loading.value ||
    loadFailed.value ||
    !!selectionError.value ||
    !selectedModel.value ||
    props.entry.status !== 'draft'
  )
    return
  submitting.value = true
  submitFailed.value = false
  errorMessage.value = ''
  try {
    if (props.beforeSubmit && !(await props.beforeSubmit())) return
    if (!mounted || props.entry.status !== 'draft') return
    const selection = {
      provider: provider.value,
      model_name: modelName.value,
      reasoning_effort: effort.value || null
    }
    try {
      localStorage.setItem(preferenceKey(), JSON.stringify(selection))
    } catch {
      /* Optional local preference. */
    }
    const response = await stockResearchApi.createGenerationTask({
      target_entry_id: props.entry.id,
      draft_kind: props.entry.entry_type,
      ...selection,
      references: references.value.map(({ kind, source_id, account_type, source_date, label }) => ({
        kind,
        source_id,
        account_type,
        source_date,
        label
      }))
    })
    if (!mounted) return
    pollCount = 0
    pollPaused.value = false
    receive(response.data)
  } catch {
    if (mounted) {
      submitFailed.value = true
      errorMessage.value = '生成任务提交失败，请重试'
    }
  } finally {
    if (mounted) submitting.value = false
  }
}
onMounted(() => {
  void loadModels()
  schedulePoll()
})
onBeforeUnmount(() => {
  mounted = false
  stopPolling()
})
</script>

<style scoped>
.el-select {
  width: 100%;
  min-width: 0;
}
.task-state {
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-wrap: anywhere;
}
.task-state small,
.generation-message {
  color: var(--el-text-color-secondary);
}
.generation-error {
  color: var(--el-color-danger);
  overflow-wrap: anywhere;
}
.generation-actions {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}
.generation-actions .el-button + .el-button {
  margin-left: 0;
}
</style>
