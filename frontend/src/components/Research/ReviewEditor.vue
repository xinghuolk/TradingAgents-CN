<template>
  <section class="review-editor">
    <el-form label-position="top" :disabled="readonly || busy">
      <div class="review-fields">
        <el-form-item label="复盘类型">
          <el-radio-group v-model="form.review_kind" :disabled="!!record" @change="changeKind">
            <el-radio-button value="routine">例行复盘</el-radio-button>
            <el-radio-button value="decision">决策复盘</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="范围">
          <el-radio-group v-model="form.scope" :disabled="!!record" @change="changeScope">
            <el-radio-button value="portfolio">组合</el-radio-button>
            <el-radio-button value="stock">个股</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </div>
      <el-form-item v-if="form.scope === 'stock'" label="证券" required>
        <el-select
          v-model="form.security_id"
          filterable
          :disabled="!!record"
          @change="securityChanged"
        >
          <el-option
            v-for="item in securities"
            :key="item.security_id"
            :value="item.security_id"
            :label="`${item.name} · ${item.security_id}`"
          />
          <el-option
            v-if="
              form.security_id && !securities.some(item => item.security_id === form.security_id)
            "
            :value="form.security_id"
            :label="form.security_id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="上下文">
        <div class="context-options">
          <el-checkbox
            v-for="option in contextOptions"
            :key="option.key"
            v-model="form.scope_metadata[option.key]"
            :disabled="!!record && record.status !== 'draft'"
            @change="contextChanged"
            >{{ option.label }}</el-checkbox
          >
        </div>
      </el-form-item>
      <template v-if="form.review_kind === 'decision'">
        <el-form-item label="历史决策（可选）">
          <el-select v-model="form.decision_id" clearable filterable @change="selectDecision">
            <el-option
              v-for="decision in decisions"
              :key="decision.id"
              :value="decision.id"
              :label="`${decision.decision_date || ''} · ${decision.title || decision.security_id || '决策'}`"
            />
            <el-option
              v-if="form.decision_id && !decisions.some(item => item.id === form.decision_id)"
              :value="form.decision_id"
              :label="`${form.decision_id}（来源暂不可用）`"
            />
          </el-select>
          <el-button v-if="decisionsFailed" text :icon="Refresh" @click="loadDecisions"
            >重试历史决策</el-button
          >
        </el-form-item>
        <details v-if="selectedDecision">
          <summary>关联决策与事实</summary>
          <ResearchMarkdownEditor :model-value="selectedDecision.body" readonly />
          <p v-for="reference in selectedDecision.references" :key="reference.source_id">
            {{ reference.label || reference.source_id }}
          </p>
        </details>
      </template>
      <el-form-item label="标题"><el-input v-model="form.title" @input="changed" /></el-form-item>
      <el-form-item label="引用资料">
        <ResearchReferencePicker
          v-model="form.references"
          :security-id="form.scope === 'stock' ? form.security_id : undefined"
          :security-ids="contextSecurityIds"
          :decision-id="form.decision_id || undefined"
          :disabled="readonly || busy"
          @update:model-value="changed"
        />
      </el-form-item>
    </el-form>
    <ResearchMarkdownEditor
      v-model="form.body"
      :state="editingRevision ? (revisionDirty ? 'dirty' : 'saved') : autosave.state.value"
      :readonly="readonly"
      :disabled="busy"
      label="复盘正文"
      :generation-entry="record"
      :generation-references="form.references"
      :before-generate="flush"
      @generated="record && (record.ai_drafts = [...record.ai_drafts, $event])"
      @update:model-value="changed"
      @retry="flush"
    />
    <div class="editor-actions">
      <template v-if="!record || record.status === 'draft'">
        <el-button :icon="DocumentAdd" :loading="busy" @click="saveDraft">保存草稿</el-button>
        <el-button type="primary" :icon="Check" :loading="busy" @click="confirmReview"
          >确认复盘</el-button
        >
      </template>
      <template v-else-if="record.status === 'confirmed'">
        <template v-if="editingRevision">
          <el-button :disabled="busy" @click="cancelRevision">取消编辑</el-button>
          <el-button type="primary" :icon="Check" :loading="busy" @click="saveRevision"
            >保存新版本</el-button
          >
        </template>
        <el-button v-else :icon="EditPen" :disabled="busy" @click="startRevision"
          >编辑新版本</el-button
        >
        <el-button
          v-if="canApplyToThesis && !editingRevision"
          :icon="Switch"
          :disabled="busy"
          @click="openDiff"
          >应用到当前论点</el-button
        >
      </template>
      <el-button v-if="record" :icon="Clock" :disabled="busy" @click="showVersions"
        >版本 {{ record.current_revision }}</el-button
      >
    </div>
    <el-dialog
      v-model="diffVisible"
      title="应用到当前论点"
      width="min(1000px, 96vw)"
      :close-on-click-modal="false"
    >
      <div class="thesis-diff">
        <section>
          <h3>当前论点</h3>
          <ResearchMarkdownEditor :model-value="currentThesis?.body || ''" readonly />
        </section>
        <section>
          <h3>应用后论点</h3>
          <ResearchMarkdownEditor v-model="proposedBody" label="应用后论点" :disabled="busy" />
        </section>
      </div>
      <template #footer
        ><el-button :disabled="busy" @click="diffVisible = false">取消</el-button>
        <el-button type="primary" :icon="Check" :loading="busy" @click="applyDiff"
          >确认应用</el-button
        ></template
      >
    </el-dialog>
    <el-dialog v-model="versionsVisible" title="复盘版本" width="min(800px, 94vw)">
      <el-empty v-if="!revisions.length" description="暂无版本" />
      <details v-for="revision in revisions" :key="revision.id" class="revision-item">
        <summary>版本 {{ revision.revision }} · {{ revision.created_at }}</summary>
        <ResearchMarkdownEditor :model-value="String(revision.snapshot.body || '')" readonly />
      </details>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, Clock, DocumentAdd, EditPen, Refresh, Switch } from '@element-plus/icons-vue'
import ResearchMarkdownEditor from '@/components/Research/ResearchMarkdownEditor.vue'
import ResearchReferencePicker from '@/components/Research/ResearchReferencePicker.vue'
import { useResearchAutosave } from '@/composables/useResearchAutosave'
import {
  stockResearchApi,
  type ResearchEntry,
  type ResearchWorkspace,
  type ResearchWorkspaceSummary,
  type ResearchReference,
  type ResearchRevision,
  type ReviewKind,
  type ResearchScope,
  type EntryPatchInput
} from '@/api/stockResearch'

const props = defineProps<{
  entry?: ResearchEntry | null
  securityId?: string
  reviewKind?: ReviewKind
}>()
const emit = defineEmits<{
  saved: [entry: ResearchEntry]
  applied: [workspace: ResearchWorkspace, revisions: ResearchRevision[]]
}>()
const template =
  '## 市场与持仓表现\n\n## 重要事实、公告和调研变化\n\n## 当前论点变化\n\n## 后续观察\n'
const contextOptions = [
  { key: 'include_market', label: '市场概况' },
  { key: 'include_real_holdings', label: '真实持仓' },
  { key: 'include_paper_holdings', label: '模拟持仓' },
  { key: 'include_funds', label: '资金' },
  { key: 'include_global_markets', label: '全球市场' },
  { key: 'include_limit_up_concepts', label: '涨停题材' },
  { key: 'include_financial_metrics', label: '财务指标' }
]
const record = ref(props.entry || null)
function defaultContext(scope: ResearchScope, kind: ReviewKind): Record<string, boolean> {
  return Object.fromEntries(
    contextOptions.map(item => [
      item.key,
      scope === 'portfolio' &&
        kind === 'routine' &&
        ['include_market', 'include_real_holdings'].includes(item.key)
    ])
  )
}
const initialScope = props.entry?.scope || (props.securityId ? 'stock' : 'portfolio')
const initialKind = props.entry?.review_kind || props.reviewKind || 'routine'
const form = reactive({
  scope: initialScope as ResearchScope,
  security_id: props.entry?.security_id || props.securityId || '',
  review_kind: initialKind as ReviewKind,
  decision_id: props.entry?.decision_id || '',
  title: props.entry?.title || '',
  body: props.entry?.body ?? template,
  references: [...(props.entry?.references || [])] as ResearchReference[],
  scope_metadata: {
    ...defaultContext(initialScope, initialKind),
    ...props.entry?.scope_metadata
  } as Record<string, boolean>
})
const busy = ref(false)
const editingRevision = ref(false)
const revisionDirty = ref(false)
const dirtyNew = ref(false)
const contextDirty = ref(false)
const readonly = computed(
  () =>
    record.value?.status === 'archived' ||
    (record.value?.status === 'confirmed' && !editingRevision.value)
)
const canApplyToThesis = computed(
  () =>
    record.value?.status === 'confirmed' &&
    !!record.value.security_id &&
    (!props.securityId || record.value.security_id === props.securityId)
)
const securities = ref<ResearchWorkspaceSummary[]>([])
const securitiesState = ref<'loading' | 'ready' | 'failed'>('loading')
let settleSources!: () => void
const sourcesSettled = new Promise<void>(resolve => {
  settleSources = resolve
})
const associationsReady = computed(
  () =>
    form.scope === 'stock' ||
    securitiesState.value === 'ready' ||
    (!form.scope_metadata.include_real_holdings && !form.scope_metadata.include_paper_holdings)
)
const decisions = ref<ResearchEntry[]>([])
const decisionsFailed = ref(false)
const selectedDecision = computed(() => decisions.value.find(item => item.id === form.decision_id))
const contextSecurityIds = computed(() =>
  form.scope === 'portfolio'
    ? securities.value
        .filter(
          item =>
            (form.scope_metadata.include_real_holdings && item.has_real_holding) ||
            (form.scope_metadata.include_paper_holdings && item.has_paper_holding)
        )
        .map(item => item.security_id)
    : []
)
function input(): EntryPatchInput {
  return {
    title: form.title,
    body: form.body,
    review_kind: form.review_kind,
    decision_id: form.decision_id || (record.value?.decision_id ? '' : undefined),
    references: form.references.map(item => ({ ...item })),
    security_ids:
      associationsReady.value &&
      (!record.value || (record.value.status === 'draft' && contextDirty.value))
        ? form.scope === 'portfolio'
          ? [...contextSecurityIds.value]
          : [form.security_id]
        : undefined,
    scope_metadata: { ...form.scope_metadata }
  }
}
const autosave = useResearchAutosave<EntryPatchInput>(async patch => {
  if (!contextActive || record.value?.status !== 'draft') return
  const id = record.value.id
  await stockResearchApi.patchEntry(id, patch)
  if (contextActive && record.value?.id === id && patch.security_ids !== undefined)
    record.value.security_ids = [...patch.security_ids]
})
async function syncDraftAssociations() {
  if (!contextActive || record.value?.status !== 'draft') return false
  if (!associationsReady.value) return true
  const ids = form.scope === 'portfolio' ? [...contextSecurityIds.value] : [form.security_id]
  const stored = record.value.security_ids || []
  if (ids.length === stored.length && ids.every((id, index) => id === stored[index])) return true
  autosave.schedule({ security_ids: ids })
  return autosave.flush()
}
function changed() {
  if (readonly.value) return
  if (editingRevision.value) revisionDirty.value = true
  else if (record.value) autosave.schedule(input())
  else dirtyNew.value = true
}
function contextChanged() {
  if (record.value && record.value.status !== 'draft') return
  contextDirty.value = true
  changed()
}
function changeKind() {
  if (record.value) return
  form.scope_metadata = defaultContext(form.scope, form.review_kind)
  form.decision_id = ''
  changed()
  void loadDecisions()
}
function changeScope() {
  form.scope_metadata = defaultContext(form.scope, form.review_kind)
  form.decision_id = ''
  changed()
  void loadDecisions()
}
function securityChanged() {
  form.decision_id = ''
  changed()
  void loadDecisions()
}
function selectDecision() {
  if (readonly.value) return
  const decision = selectedDecision.value
  if (decision) {
    const values = [
      ...form.references,
      {
        kind: 'decision' as const,
        source_id: decision.id,
        label: decision.title || decision.security_id
      },
      ...decision.references
    ]
    const unique = new Map(
      values.map(item => [
        JSON.stringify([item.kind, item.source_id, item.account_type, item.source_date]),
        item
      ])
    )
    form.references = [...unique.values()]
  }
  changed()
}
async function loadDecisions() {
  if (form.review_kind !== 'decision') return
  try {
    decisions.value = (
      await stockResearchApi.listEntries({
        entry_type: 'decision',
        status: 'confirmed',
        security_id: form.scope === 'stock' ? form.security_id || undefined : undefined,
        page_size: 100
      })
    ).data.items
    decisionsFailed.value = false
  } catch {
    decisionsFailed.value = true
  }
}
let pendingSave: Promise<boolean> | undefined
function save(): Promise<boolean> {
  if (pendingSave) return pendingSave
  pendingSave = persistDraft().finally(() => {
    pendingSave = undefined
  })
  return pendingSave
}
async function persistDraft() {
  if (!contextActive || record.value?.status === 'archived') return false
  if (!record.value) {
    if (form.scope === 'stock' && !form.security_id) {
      ElMessage.warning('请选择证券')
      return false
    }
    const response = await stockResearchApi.createEntry({
      entry_type: 'review',
      scope: form.scope,
      security_id: form.scope === 'stock' ? form.security_id : undefined,
      ...input()
    })
    if (!contextActive) return false
    record.value = response.data
    contextDirty.value = true
    dirtyNew.value = false
    emit('saved', record.value)
  }
  if (!(await autosave.flush()) || !contextActive) return false
  return contextDirty.value ? syncDraftAssociations() : true
}
async function flush() {
  if (busy.value) return false
  if (editingRevision.value && revisionDirty.value) {
    ElMessage.warning('请保存新版本或取消编辑')
    return false
  }
  try {
    return dirtyNew.value ? await save() : await autosave.flush()
  } catch {
    ElMessage.error('复盘保存失败')
    return false
  }
}
async function saveDraft() {
  if (busy.value || readonly.value) return
  busy.value = true
  try {
    if (await save()) ElMessage.success('草稿已保存')
  } catch {
    ElMessage.error('复盘保存失败')
  } finally {
    busy.value = false
  }
}
async function confirmReview() {
  if (busy.value || record.value?.status === 'archived' || record.value?.status === 'confirmed')
    return
  busy.value = true
  try {
    if (!associationsReady.value && securitiesState.value === 'loading') await sourcesSettled
    if (!contextActive) return
    if (!(await save()) || !record.value) return
    if (!(await syncDraftAssociations()) || !contextActive) return
    record.value = (await stockResearchApi.confirmEntry(record.value.id)).data
    if (!contextActive) return
    emit('saved', record.value)
    await loadVersions()
    ElMessage.success('复盘已确认')
  } catch {
    ElMessage.error('复盘确认失败')
  } finally {
    busy.value = false
  }
}
function startRevision() {
  if (record.value?.status === 'confirmed') editingRevision.value = true
}
function cancelRevision() {
  if (!record.value) return
  form.title = record.value.title
  form.body = record.value.body
  form.references = [...record.value.references]
  form.decision_id = record.value.decision_id || ''
  form.scope_metadata = {
    ...defaultContext(form.scope, form.review_kind),
    ...record.value.scope_metadata
  } as Record<string, boolean>
  editingRevision.value = false
  revisionDirty.value = false
}
async function saveRevision() {
  if (busy.value || record.value?.status !== 'confirmed' || !editingRevision.value) return
  busy.value = true
  try {
    record.value = (await stockResearchApi.patchEntry(record.value.id, input())).data
    editingRevision.value = false
    revisionDirty.value = false
    emit('saved', record.value)
    await loadVersions()
    ElMessage.success('新版本已保存')
  } catch {
    ElMessage.error('新版本保存失败')
  } finally {
    busy.value = false
  }
}
const revisions = ref<ResearchRevision[]>([])
const versionsVisible = ref(false)
async function loadVersions() {
  if (record.value)
    revisions.value = (await stockResearchApi.listRevisions('entry', record.value.id)).data
}
async function showVersions() {
  if (!(await flush())) return
  try {
    await loadVersions()
    versionsVisible.value = true
  } catch {
    ElMessage.error('版本加载失败')
  }
}
const diffVisible = ref(false)
const currentThesis = ref<ResearchWorkspace | null>(null)
const proposedBody = ref('')
async function openDiff() {
  if (
    busy.value ||
    !canApplyToThesis.value ||
    record.value?.status !== 'confirmed' ||
    !record.value.security_id ||
    editingRevision.value
  )
    return
  busy.value = true
  try {
    currentThesis.value = (await stockResearchApi.getWorkspace(record.value.security_id)).data
    const section = form.body.split('## 当前论点变化')[1]?.split('\n## ')[0]?.trim()
    proposedBody.value = section || currentThesis.value.body
    diffVisible.value = true
  } catch {
    ElMessage.error('论点加载失败')
  } finally {
    busy.value = false
  }
}
async function applyDiff() {
  if (
    busy.value ||
    !diffVisible.value ||
    !canApplyToThesis.value ||
    record.value?.status !== 'confirmed' ||
    !record.value.security_id
  )
    return
  busy.value = true
  try {
    await stockResearchApi.applyReviewToThesis(record.value.id, { body: proposedBody.value })
    currentThesis.value = (await stockResearchApi.getWorkspace(record.value.security_id)).data
    const versions = (await stockResearchApi.listRevisions('workspace', record.value.security_id))
      .data
    emit('applied', currentThesis.value, versions)
    diffVisible.value = false
    ElMessage.success('当前论点已更新')
  } catch {
    ElMessage.error('应用失败，请重试')
  } finally {
    busy.value = false
  }
}
let contextActive = true
onBeforeUnmount(() => {
  contextActive = false
  settleSources()
})
onMounted(async () => {
  try {
    const response = await stockResearchApi.listWorkspaces({ page_size: 100 })
    if (!contextActive) return
    securities.value = response.data.items
    securitiesState.value = 'ready'
    if (contextDirty.value && record.value?.status === 'draft') changed()
  } catch {
    if (!contextActive) return
    securitiesState.value = 'failed'
    ElMessage.warning('证券范围加载失败')
  } finally {
    settleSources()
  }
  await loadDecisions()
})
const dirty = computed(
  () => dirtyNew.value || revisionDirty.value || autosave.state.value !== 'saved'
)
defineExpose({ flush, dirty })
</script>

<style scoped>
.review-editor {
  min-width: 0;
}
.review-fields,
.thesis-diff {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
}
.review-editor :deep(.el-select) {
  width: 100%;
}
.context-options {
  display: flex;
  flex-wrap: wrap;
  gap: 0 16px;
}
.context-options .el-checkbox {
  margin-right: 0;
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
.thesis-diff section {
  min-width: 0;
}
.thesis-diff h3 {
  margin: 0 0 12px;
  font-size: 16px;
}
.revision-item {
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-light);
}
@media (max-width: 640px) {
  .review-fields,
  .thesis-diff {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }
}
</style>
