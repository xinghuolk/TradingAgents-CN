<template>
  <section class="decision-editor">
    <el-form label-position="top" :disabled="readonly || busy">
      <div class="decision-fields">
        <el-form-item label="行动" required>
          <el-select v-model="form.decision_action" aria-label="行动" @change="changed">
            <el-option
              v-for="(label, value) in actions"
              :key="value"
              :label="label"
              :value="value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="日期" required>
          <el-date-picker
            v-model="form.decision_date"
            type="date"
            value-format="YYYY-MM-DD"
            aria-label="日期"
            @change="changed"
          />
        </el-form-item>
      </div>
      <el-form-item label="标题"><el-input v-model="form.title" @input="changed" /></el-form-item>
      <div class="decision-fields">
        <el-form-item label="计划价格"
          ><el-input v-model="form.planned_price" @input="changed"
        /></el-form-item>
        <el-form-item label="目标仓位"
          ><el-input v-model="form.target_allocation" @input="changed"
        /></el-form-item>
        <el-form-item label="预期周期"
          ><el-input v-model="form.horizon" @input="changed"
        /></el-form-item>
      </div>
      <el-form-item label="引用资料与交易关联">
        <ResearchReferencePicker
          v-model="form.references"
          :security-id="securityId"
          :decision-id="record?.id"
          :disabled="readonly || busy"
          @update:model-value="changed"
        />
      </el-form-item>
    </el-form>
    <ResearchMarkdownEditor
      v-model="form.body"
      :state="autosave.state.value"
      :readonly="readonly"
      :disabled="busy"
      label="决策正文"
      :generation-entry="record"
      :generation-references="form.references"
      :before-generate="flush"
      @generated="record && (record.ai_drafts = [...record.ai_drafts, $event])"
      @update:model-value="changed"
      @retry="flush"
    />
    <div class="editor-actions">
      <template v-if="!readonly">
        <el-button :icon="DocumentAdd" :loading="busy" @click="saveDraft">保存草稿</el-button>
        <el-button type="primary" :icon="Check" :loading="busy" @click="prepareConfirm"
          >确认决策</el-button
        >
      </template>
      <el-button v-else-if="record?.status === 'confirmed'" :icon="Plus" @click="emit('new')"
        >创建新决策</el-button
      >
    </div>
    <details v-if="record?.thesis_snapshot" class="snapshot">
      <summary>确认时论点快照</summary>
      <ResearchMarkdownEditor :model-value="String(record.thesis_snapshot.body || '')" readonly />
      <dl v-for="field in snapshotFields" :key="field.key">
        <dt>{{ field.label }}</dt>
        <dd>{{ snapshotText(record.thesis_snapshot[field.key]) }}</dd>
      </dl>
    </details>
    <el-dialog
      v-model="confirmVisible"
      title="确认决策"
      width="min(760px, 94vw)"
      :close-on-click-modal="false"
    >
      <h3>当前论点快照</h3>
      <ResearchMarkdownEditor :model-value="snapshot?.body || ''" readonly />
      <dl v-for="field in snapshotFields" :key="field.key">
        <dt>{{ field.label }}</dt>
        <dd>{{ snapshotText(snapshot?.[field.key]) }}</dd>
      </dl>
      <template #footer
        ><el-button :disabled="busy" @click="confirmVisible = false">取消</el-button>
        <el-button type="primary" :icon="Check" :loading="busy" @click="confirmDecision"
          >确认并记录</el-button
        ></template
      >
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, DocumentAdd, Plus } from '@element-plus/icons-vue'
import ResearchMarkdownEditor from '@/components/Research/ResearchMarkdownEditor.vue'
import ResearchReferencePicker from '@/components/Research/ResearchReferencePicker.vue'
import { useResearchAutosave } from '@/composables/useResearchAutosave'
import {
  stockResearchApi,
  type ResearchEntry,
  type ResearchWorkspace,
  type ResearchReference,
  type DecisionAction,
  type EntryPatchInput
} from '@/api/stockResearch'

const props = defineProps<{ entry?: ResearchEntry | null; securityId: string }>()
const emit = defineEmits<{ saved: [entry: ResearchEntry]; new: [] }>()
const record = ref(props.entry || null)
const date = new Date()
const today = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
const form = reactive({
  title: props.entry?.title || '',
  body: props.entry?.body || '',
  decision_action: props.entry?.decision_action || ('observe' as DecisionAction),
  decision_date: props.entry?.decision_date || today,
  planned_price: props.entry?.planned_price || '',
  target_allocation: props.entry?.target_allocation || '',
  horizon: props.entry?.horizon || '',
  references: [...(props.entry?.references || [])] as ResearchReference[]
})
const actions = { buy: '买入', add: '加仓', reduce: '减仓', sell: '卖出', observe: '继续观察' }
const busy = ref(false)
const readonly = computed(() => !!record.value && record.value.status !== 'draft')
const confirmVisible = ref(false)
const snapshot = ref<ResearchWorkspace | null>(null)
const snapshotFields = [
  { key: 'assumptions', label: '关键假设' },
  { key: 'risks', label: '风险' },
  { key: 'invalidation_conditions', label: '失效条件' },
  { key: 'open_questions', label: '待解问题' }
] as const
function snapshotText(value: unknown) {
  return Array.isArray(value) ? value.join('；') || '暂无' : '暂无'
}
function input(): EntryPatchInput {
  return { ...form, references: form.references.map(item => ({ ...item })) }
}
const autosave = useResearchAutosave<EntryPatchInput>(async patch => {
  if (!record.value || readonly.value) return
  if (!patch.decision_action || !patch.decision_date) throw new Error('行动和日期不能为空')
  await stockResearchApi.patchEntry(record.value.id, patch)
})
const dirtyNew = ref(false)
function changed() {
  if (readonly.value) return
  if (record.value) autosave.schedule(input())
  else dirtyNew.value = true
}
async function save() {
  if (readonly.value) return true
  if (!form.decision_action || !form.decision_date) {
    ElMessage.warning('请选择行动和日期')
    return false
  }
  if (!record.value) {
    record.value = (
      await stockResearchApi.createEntry({
        entry_type: 'decision',
        security_id: props.securityId,
        ...input()
      })
    ).data
    dirtyNew.value = false
    emit('saved', record.value)
  }
  return autosave.flush()
}
async function flush() {
  if (busy.value) return false
  try {
    return dirtyNew.value ? await save() : await autosave.flush()
  } catch {
    ElMessage.error('决策保存失败')
    return false
  }
}
async function saveDraft() {
  if (busy.value || readonly.value) return
  busy.value = true
  try {
    if (await save()) ElMessage.success('草稿已保存')
  } catch {
    ElMessage.error('决策保存失败')
  } finally {
    busy.value = false
  }
}
async function prepareConfirm() {
  if (busy.value || readonly.value) return
  busy.value = true
  try {
    if (!(await save())) return
    snapshot.value = (await stockResearchApi.getWorkspace(props.securityId)).data
    confirmVisible.value = true
  } catch {
    ElMessage.error('无法加载论点快照')
  } finally {
    busy.value = false
  }
}
async function confirmDecision() {
  if (busy.value || readonly.value || !confirmVisible.value || !record.value) return
  busy.value = true
  try {
    record.value = (await stockResearchApi.confirmEntry(record.value.id)).data
    confirmVisible.value = false
    emit('saved', record.value)
    ElMessage.success('决策已确认')
  } catch {
    ElMessage.error('决策确认失败')
  } finally {
    busy.value = false
  }
}
const dirty = computed(() => dirtyNew.value || autosave.state.value !== 'saved')
defineExpose({ flush, dirty })
</script>

<style scoped>
.decision-editor {
  min-width: 0;
}
.decision-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 20px;
}
.decision-fields :deep(.el-select),
.decision-fields :deep(.el-date-editor) {
  width: 100%;
}
.editor-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 20px;
}
.editor-actions .el-button + .el-button {
  margin-left: 0;
}
.snapshot {
  margin-top: 24px;
}
dt {
  color: var(--el-text-color-secondary);
}
dd {
  margin: 6px 0 14px;
  overflow-wrap: anywhere;
}
@media (max-width: 640px) {
  .decision-fields {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
