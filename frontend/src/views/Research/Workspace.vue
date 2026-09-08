<template>
  <section class="research-workspace" v-loading="loading">
    <header class="workspace-header">
      <div class="security-heading">
        <h1>{{ workspace?.name || code }}</h1>
        <span
          >{{ workspace?.code || code
          }}<template v-if="market"> · {{ marketLabels[market] }}</template></span
        >
      </div>
      <div class="header-actions">
        <el-button :icon="ArrowLeft" :disabled="busy" @click="returnToStock">返回行情</el-button>
        <template v-if="workspace">
          <el-button
            v-if="section === 'thesis'"
            :icon="DocumentAdd"
            :disabled="busy"
            @click="saveVersion"
            >保存版本</el-button
          >
          <el-dropdown :disabled="busy" @command="openCreate">
            <el-button type="primary" :icon="Plus" :disabled="busy"
              >新建<el-icon class="dropdown-icon"><ArrowDown /></el-icon
            ></el-button>
            <template #dropdown
              ><el-dropdown-menu
                ><el-dropdown-item command="note">笔记</el-dropdown-item
                ><el-dropdown-item command="research">调研</el-dropdown-item
                ><el-dropdown-item command="decision">决策</el-dropdown-item
                ><el-dropdown-item command="routine">例行复盘</el-dropdown-item
                ><el-dropdown-item command="decision-review"
                  >决策复盘</el-dropdown-item
                ></el-dropdown-menu
              ></template
            >
          </el-dropdown>
        </template>
      </div>
    </header>
    <div v-if="!market" class="market-prompt">
      <label for="research-market">市场</label>
      <el-select id="research-market" placeholder="选择市场" @change="chooseMarket"
        ><el-option
          v-for="(label, value) in marketLabels"
          :key="value"
          :label="label"
          :value="value"
      /></el-select>
    </div>
    <el-result v-else-if="loadError" icon="error" title="工作区加载失败"
      ><template #extra
        ><el-button :icon="RefreshRight" @click="loadWorkspace">重试</el-button></template
      ></el-result
    >
    <div v-else-if="workspace" class="workspace-layout">
      <nav class="workspace-nav" aria-label="研究章节">
        <button
          v-for="item in sections"
          :key="item.key"
          type="button"
          :class="{ active: section === item.key }"
          :aria-current="section === item.key ? 'page' : undefined"
          :disabled="busy"
          @click="switchSection(item.key)"
        >
          <el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span>
        </button>
        <button type="button" :disabled="busy" class="trash-nav" @click="openTrash">
          <el-icon><Delete /></el-icon><span>回收站</span>
        </button>
      </nav>
      <main class="workspace-document">
        <div class="document-heading">
          <div class="document-title">
            <el-button
              v-if="entry || pendingEditor"
              :icon="ArrowLeft"
              circle
              title="返回记录列表"
              aria-label="返回记录列表"
              :disabled="busy"
              @click="backToList"
            />
            <h2>{{ sectionLabel }}</h2>
            <el-tag v-if="entry" size="small" effect="plain">{{
              statusLabels[entry.status]
            }}</el-tag>
          </div>
          <div v-if="section === 'thesis' || entry" class="document-actions">
            <el-tooltip content="版本历史"
              ><el-button
                :icon="Clock"
                circle
                aria-label="版本历史"
                :disabled="busy"
                @click="openVersions"
            /></el-tooltip>
            <el-dropdown v-if="entry" :disabled="busy" @command="entryAction">
              <el-button
                :icon="MoreFilled"
                circle
                title="记录操作"
                aria-label="记录操作"
                :disabled="busy"
              />
              <template #dropdown
                ><el-dropdown-menu>
                  <el-dropdown-item v-if="editableEntry && documentEntry" command="convert">{{
                    entry.entry_type === 'note' ? '转为调研' : '转为笔记'
                  }}</el-dropdown-item>
                  <el-dropdown-item v-if="entry.status !== 'archived'" command="archive"
                    >归档</el-dropdown-item
                  >
                  <el-dropdown-item command="delete" divided>移入回收站</el-dropdown-item>
                </el-dropdown-menu></template
              >
            </el-dropdown>
          </div>
        </div>
        <fieldset v-if="section === 'thesis'" class="document-fields" :disabled="busy">
          <ResearchMarkdownEditor
            :model-value="workspace.body"
            :state="autosave.state.value"
            :disabled="busy"
            label="当前论点正文"
            @update:model-value="updateWorkspaceBody"
            @retry="retrySave"
          />
          <div class="thesis-details">
            <section v-for="field in thesisFields" :key="field.key" class="thesis-field">
              <div class="field-heading">
                <h3>{{ field.label }}</h3>
                <el-button
                  :icon="Plus"
                  text
                  circle
                  :aria-label="`添加${field.label}`"
                  :title="`添加${field.label}`"
                  :disabled="busy"
                  @click="workspace[field.key].push('')"
                />
              </div>
              <div v-for="(_, index) in workspace[field.key]" :key="index" class="list-field-row">
                <el-input
                  v-model="workspace[field.key][index]"
                  :aria-label="`${field.label} ${index + 1}`"
                  :disabled="busy"
                  @input="scheduleWorkspace"
                />
                <el-button
                  :icon="Close"
                  text
                  circle
                  :aria-label="`删除${field.label} ${index + 1}`"
                  :disabled="busy"
                  @click="removeThesisItem(field.key, index)"
                />
              </div>
              <span v-if="!workspace[field.key].length" class="muted">暂无</span>
            </section>
          </div>
        </fieldset>
        <DecisionEditor
          v-else-if="pendingEditor === 'decision' || entry?.entry_type === 'decision'"
          :key="editorSession"
          ref="formalEditor"
          :entry="entry"
          :security-id="workspace.security_id"
          @saved="formalSaved"
          @new="openCreate('decision')"
        />
        <ReviewEditor
          v-else-if="pendingEditor === 'review' || entry?.entry_type === 'review'"
          :key="editorSession"
          ref="formalEditor"
          :entry="entry"
          :security-id="workspace.security_id"
          :review-kind="newReviewKind"
          @saved="formalSaved"
          @applied="reviewApplied"
        />
        <template v-else-if="entry">
          <fieldset class="document-fields" :disabled="busy || !editableEntry">
            <el-form label-position="top">
              <el-form-item
                label="标题"
                :required="entry.entry_type === 'note'"
                :error="noteInvalid ? '笔记标题和正文不能为空' : ''"
                ><el-input
                  v-if="editableEntry"
                  v-model="entry.title"
                  :disabled="busy"
                  @input="scheduleEntry"
                />
                <h3 v-else class="readonly-title">
                  {{ entry.title || sectionLabel }}
                </h3></el-form-item
              >
              <el-form-item v-if="entry.entry_type === 'research'" label="研究主题"
                ><el-input
                  :model-value="entry.topic || ''"
                  :disabled="busy || !editableEntry"
                  @update:model-value="updateTopic"
              /></el-form-item>
            </el-form>
            <ResearchMarkdownEditor
              :key="entry.id"
              :model-value="entry.body"
              :state="autosave.state.value"
              :disabled="busy"
              :readonly="!editableEntry"
              @update:model-value="updateEntryBody"
              @retry="retrySave"
            />
            <el-form
              v-if="entry.entry_type === 'research'"
              class="research-fields"
              label-position="top"
            >
              <el-form-item label="引用资料"
                ><el-select
                  :model-value="entry.references.map(referenceKey)"
                  multiple
                  filterable
                  :disabled="busy || !editableEntry"
                  :loading="referencesLoading"
                  class="reference-select"
                  @change="setReferences"
                  ><el-option
                    v-for="option in referenceOptions"
                    :key="referenceKey(option)"
                    :value="referenceKey(option)"
                    :label="option.label || option.source_id" /></el-select
              ></el-form-item>
              <el-form-item label="结论"
                ><el-input
                  :model-value="entry.conclusion || ''"
                  type="textarea"
                  :rows="4"
                  :disabled="busy || !editableEntry"
                  @update:model-value="updateConclusion"
              /></el-form-item>
            </el-form>
          </fieldset>
        </template>
        <ResearchEntryList
          v-else
          :entries="entries"
          :loading="entriesLoading"
          :error="entriesError"
          :disabled="busy"
          :total="entryTotal"
          :page="entryPage"
          :page-size="pageSize"
          :status="entryStatus"
          @select="openEntry"
          @status="changeStatus"
          @page="changePage"
          @retry="changePage(entryPage)"
        />
      </main>
    </div>
    <el-dialog
      v-model="createVisible"
      :title="createType === 'note' ? '新建笔记' : '新建调研'"
      width="min(720px, 94vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!busy"
      :show-close="!busy"
    >
      <el-form label-position="top" @submit.prevent="createEntry">
        <el-form-item label="标题" :required="createType === 'note'"
          ><el-input v-model="createForm.title" :disabled="busy"
        /></el-form-item>
        <el-form-item v-if="createType === 'research'" label="研究主题"
          ><el-input v-model="createForm.topic" :disabled="busy"
        /></el-form-item>
        <el-form-item label="正文" :required="createType === 'note'"
          ><el-input v-model="createForm.body" type="textarea" :rows="9" :disabled="busy"
        /></el-form-item>
      </el-form>
      <template #footer
        ><el-button :disabled="busy" @click="createVisible = false">取消</el-button
        ><el-button type="primary" :loading="busy" @click="createEntry">创建</el-button></template
      >
    </el-dialog>
    <el-dialog
      v-model="versionsVisible"
      title="版本历史"
      width="min(960px, 94vw)"
      :close-on-click-modal="false"
    >
      <div v-loading="versionsLoading" class="versions-layout">
        <div class="revision-list">
          <el-empty
            v-if="!versionsLoading && !revisions.length"
            description="暂无版本"
            :image-size="64"
          />
          <button
            v-for="revision in revisions"
            :key="revision.id"
            type="button"
            :class="{ selected: selectedRevision?.id === revision.id }"
            :disabled="busy"
            @click="selectedRevision = revision"
          >
            <strong>版本 {{ revision.revision }}</strong
            ><span>{{
              String(
                revision.snapshot.label || revisionReasonLabels[revision.reason] || '已保存版本'
              )
            }}</span
            ><small>{{ formatDateTime(revision.created_at) }}</small>
          </button>
        </div>
        <div v-if="selectedRevision" class="revision-document">
          <h3>{{ String(selectedRevision.snapshot.title || '当前论点') }}</h3>
          <ResearchMarkdownEditor
            :model-value="String(selectedRevision.snapshot.body || '')"
            readonly
          />
          <dl class="snapshot-fields">
            <template v-for="field in snapshotFields" :key="field.key"
              ><dt>{{ field.label }}</dt>
              <dd>{{ snapshotValue(selectedRevision.snapshot[field.key]) }}</dd></template
            >
          </dl>
        </div>
      </div>
      <template #footer
        ><el-button :disabled="busy" @click="versionsVisible = false">关闭</el-button
        ><el-button
          v-if="!entry || (entry.entry_type !== 'decision' && entry.status !== 'archived')"
          type="primary"
          :disabled="!selectedRevision || busy"
          @click="restoreVersion"
          >恢复为新版本</el-button
        ></template
      >
    </el-dialog>
    <el-dialog
      v-model="trashVisible"
      title="回收站 · 全部证券"
      width="min(820px, 94vw)"
      :close-on-click-modal="false"
    >
      <div v-loading="trashLoading" class="trash-list">
        <el-empty
          v-if="!trashLoading && !trashEntries.length"
          description="回收站为空"
          :image-size="64"
        />
        <div v-for="item in trashEntries" :key="item.id" class="trash-row">
          <div>
            <strong>{{ item.title || entryLabels[item.entry_type] }}</strong>
            <p>
              {{ item.security_id || '组合' }} · {{ entryLabels[item.entry_type] }} ·
              {{ formatDateTime(item.deleted_at) }}
            </p>
          </div>
          <div class="trash-actions">
            <el-button :icon="RefreshLeft" :disabled="busy" @click="restoreTrash(item)"
              >恢复</el-button
            ><el-button
              type="danger"
              plain
              :icon="Delete"
              :disabled="busy"
              @click="permanentlyDelete(item)"
              >永久删除</el-button
            >
          </div>
        </div>
      </div>
      <el-pagination
        v-if="trashTotal > pageSize"
        :current-page="trashPage"
        :page-size="pageSize"
        :total="trashTotal"
        layout="prev, pager, next"
        :disabled="busy"
        @current-change="changeTrashPage"
      />
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowDown,
  ArrowLeft,
  Clock,
  Close,
  Delete,
  Document,
  DocumentAdd,
  EditPen,
  Flag,
  MoreFilled,
  Plus,
  RefreshLeft,
  RefreshRight,
  Search
} from '@element-plus/icons-vue'
import ResearchMarkdownEditor from '@/components/Research/ResearchMarkdownEditor.vue'
import ResearchEntryList from '@/components/Research/ResearchEntryList.vue'
import DecisionEditor from '@/components/Research/DecisionEditor.vue'
import ReviewEditor from '@/components/Research/ReviewEditor.vue'
import { useResearchAutosave } from '@/composables/useResearchAutosave'
import { formatDateTime } from '@/utils/datetime'
import {
  stockResearchApi,
  type EntryPatchInput,
  type ResearchEntry,
  type ResearchEntryStatus,
  type ResearchEntryType,
  type ResearchMarket,
  type ResearchReference,
  type ResearchReferenceCandidate,
  type ResearchRevision,
  type ResearchWorkspace,
  type ReviewKind,
  type WorkspacePatchInput
} from '@/api/stockResearch'

type Section = 'thesis' | ResearchEntryType
type ThesisListKey =
  | 'assumptions'
  | 'risks'
  | 'invalidation_conditions'
  | 'open_questions'
  | 'tags'
  | 'external_links'
type SaveRequest =
  | { kind: 'workspace'; id: string; patch: WorkspacePatchInput }
  | { kind: 'entry'; id: string; type: ResearchEntryType; patch: EntryPatchInput }
const route = useRoute()
const pendingEditor = ref<'decision' | 'review' | null>(null)
const newReviewKind = ref<ReviewKind>('routine')
const editorSession = ref(0)
const formalEditor = ref<{ flush: () => Promise<boolean>; dirty: boolean } | null>(null)
function formalSaved(value: ResearchEntry) {
  entry.value = value
}
function reviewApplied(value: ResearchWorkspace) {
  workspace.value = value
}
const router = useRouter()
const code = computed(() => String(route.params.code || ''))
const market = computed<ResearchMarket | null>(() => {
  const value = route.query.market
  return value === 'CN' || value === 'HK' || value === 'US' ? value : null
})
const marketLabels = { CN: 'A 股', HK: '港股', US: '美股' }
const entryLabels = { note: '笔记', research: '调研', decision: '决策', review: '复盘' }
const statusLabels = { draft: '草稿', confirmed: '已确认', archived: '已归档' }
const revisionReasonLabels: Record<string, string> = {
  manual: '手动保存',
  revision_restored: '恢复版本',
  decision_confirmed: '决策确认',
  review_confirmed: '复盘确认'
}
const sections = [
  { key: 'thesis' as const, label: '当前论点', icon: Flag },
  { key: 'note' as const, label: '笔记', icon: EditPen },
  { key: 'research' as const, label: '调研', icon: Search },
  { key: 'decision' as const, label: '决策', icon: Document },
  { key: 'review' as const, label: '复盘', icon: RefreshLeft }
]
const thesisFields: Array<{ key: ThesisListKey; label: string }> = [
  { key: 'assumptions', label: '关键假设' },
  { key: 'risks', label: '风险' },
  { key: 'invalidation_conditions', label: '失效条件' },
  { key: 'open_questions', label: '待解问题' },
  { key: 'tags', label: '标签' },
  { key: 'external_links', label: '外部链接' }
]
const snapshotFields = [
  ...thesisFields,
  { key: 'topic', label: '研究主题' },
  { key: 'references', label: '引用资料' },
  { key: 'conclusion', label: '结论' }
]
const workspace = ref<ResearchWorkspace | null>(null)
const section = ref<Section>('thesis')
const sectionLabel = computed(() => sections.find(item => item.key === section.value)?.label)
const entry = ref<ResearchEntry | null>(null)
const documentEntry = computed(
  () => entry.value?.entry_type === 'note' || entry.value?.entry_type === 'research'
)
const editableEntry = computed(() => documentEntry.value && entry.value?.status !== 'archived')
const noteInvalid = computed(
  () =>
    entry.value?.entry_type === 'note' && (!entry.value.title.trim() || !entry.value.body.trim())
)
const loading = ref(false)
const loadError = ref(false)
const busy = ref(false)
const entriesLoading = ref(false)
const entriesError = ref(false)
const entries = ref<ResearchEntry[]>([])
const entryTotal = ref(0)
const entryPage = ref(1)
const entryStatus = ref<ResearchEntryStatus | ''>('')
const pageSize = 20
let workspaceRequest = 0
let entriesRequest = 0

const autosave = useResearchAutosave<SaveRequest>(async request => {
  if (request.kind === 'workspace')
    return stockResearchApi.patchWorkspace(request.id, request.patch)
  if (request.type === 'note' && (!request.patch.title?.trim() || !request.patch.body?.trim()))
    throw new Error('笔记标题和正文不能为空')
  return stockResearchApi.patchEntry(request.id, request.patch)
})
function scheduleWorkspace() {
  if (!workspace.value) return
  const patch: WorkspacePatchInput = { body: workspace.value.body }
  for (const { key } of thesisFields) patch[key] = [...workspace.value[key]]
  autosave.schedule({ kind: 'workspace', id: workspace.value.security_id, patch })
}
function updateWorkspaceBody(value: string) {
  if (!workspace.value) return
  workspace.value.body = value
  scheduleWorkspace()
}
function removeThesisItem(key: ThesisListKey, index: number) {
  if (!workspace.value) return
  workspace.value[key].splice(index, 1)
  scheduleWorkspace()
}
function updateEntryBody(value: string) {
  if (!entry.value) return
  entry.value.body = value
  scheduleEntry()
}
function updateTopic(value: string) {
  if (!entry.value) return
  entry.value.topic = value
  scheduleEntry()
}
function updateConclusion(value: string) {
  if (!entry.value) return
  entry.value.conclusion = value
  scheduleEntry()
}
function scheduleEntry() {
  if (!entry.value || !editableEntry.value) return
  const value = entry.value
  autosave.schedule({
    kind: 'entry',
    id: value.id,
    type: value.entry_type,
    patch: {
      title: value.title,
      body: value.body,
      topic: value.topic || '',
      conclusion: value.conclusion || '',
      references: value.references.map(reference => ({ ...reference }))
    }
  })
}
async function flushChanges() {
  if (formalEditor.value && !(await formalEditor.value.flush())) return false
  const saved = await autosave.flush()
  if (!saved)
    ElMessage.warning(noteInvalid.value ? '笔记标题和正文不能为空' : '保存失败，请重试后继续')
  return saved
}
async function retrySave() {
  await flushChanges()
}
async function transition(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  try {
    if (await flushChanges()) await action()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error('操作失败，请重试')
  } finally {
    busy.value = false
  }
}
async function loadWorkspace() {
  const request = ++workspaceRequest
  ++entriesRequest
  workspace.value = null
  entry.value = null
  pendingEditor.value = null
  section.value = 'thesis'
  entries.value = []
  versionsVisible.value = false
  trashVisible.value = false
  createVisible.value = false
  loadError.value = false
  if (!market.value) {
    loading.value = false
    return
  }
  loading.value = true
  try {
    const response = await stockResearchApi.createWorkspace({
      market: market.value,
      code: code.value,
      name: String(route.query.name || code.value)
    })
    if (request === workspaceRequest) workspace.value = response.data
  } catch {
    if (request === workspaceRequest) loadError.value = true
  } finally {
    if (request === workspaceRequest) loading.value = false
  }
}
async function loadEntries() {
  if (!workspace.value || section.value === 'thesis') return
  const request = ++entriesRequest
  entriesLoading.value = true
  entriesError.value = false
  entries.value = []
  try {
    const response = await stockResearchApi.listEntries({
      security_id: workspace.value.security_id,
      entry_type: section.value,
      status: entryStatus.value || undefined,
      page: entryPage.value,
      page_size: pageSize
    })
    if (request === entriesRequest) {
      entries.value = response.data.items
      entryTotal.value = response.data.total
      if (!entries.value.length && entryPage.value > 1) {
        entryPage.value -= 1
        await loadEntries()
      }
    }
  } catch (error) {
    if (request === entriesRequest) entriesError.value = true
    throw error
  } finally {
    if (request === entriesRequest) entriesLoading.value = false
  }
}
async function switchSection(value: Section) {
  await transition(async () => {
    section.value = value
    entry.value = null
    pendingEditor.value = null
    entryPage.value = 1
    entryStatus.value = ''
    entries.value = []
    entryTotal.value = 0
    if (value !== 'thesis') await loadEntries()
  })
}
async function openEntry(value: ResearchEntry) {
  await transition(async () => {
    entry.value = (await stockResearchApi.getEntry(value.id)).data
    pendingEditor.value = null
    editorSession.value += 1
    if (entry.value.entry_type === 'research') await loadReferences()
  })
}
async function backToList() {
  await transition(async () => {
    entry.value = null
    pendingEditor.value = null
    await loadEntries()
  })
}
async function changeStatus(value: ResearchEntryStatus | '') {
  await transition(async () => {
    entryStatus.value = value
    entryPage.value = 1
    await loadEntries()
  })
}
async function changePage(value: number) {
  await transition(async () => {
    entryPage.value = value
    await loadEntries()
  })
}
function chooseMarket(value: ResearchMarket) {
  void router.replace({ query: { ...route.query, market: value } })
}
function returnToStock() {
  void router.push({
    name: 'StockDetail',
    params: { code: code.value },
    query: market.value ? { market: market.value } : {}
  })
}

const createVisible = ref(false)
const createType = ref<'note' | 'research'>('note')
const createForm = reactive({ title: '', body: '', topic: '' })
async function openCreate(type: 'note' | 'research' | 'decision' | 'routine' | 'decision-review') {
  await transition(async () => {
    if (type === 'decision' || type === 'routine' || type === 'decision-review') {
      pendingEditor.value = type === 'decision' ? 'decision' : 'review'
      newReviewKind.value = type === 'decision-review' ? 'decision' : 'routine'
      section.value = pendingEditor.value
      entry.value = null
      editorSession.value += 1
      return
    }
    createType.value = type
    Object.assign(createForm, { title: '', body: '', topic: '' })
    createVisible.value = true
  })
}
async function createEntry() {
  if (!workspace.value) return
  if (createType.value === 'note' && (!createForm.title.trim() || !createForm.body.trim())) {
    ElMessage.warning('请填写笔记标题和正文')
    return
  }
  await transition(async () => {
    const response = await stockResearchApi.createEntry({
      entry_type: createType.value,
      security_id: workspace.value!.security_id,
      ...createForm
    })
    section.value = createType.value
    pendingEditor.value = null
    entry.value = response.data
    entryPage.value = 1
    entryStatus.value = ''
    createVisible.value = false
    if (createType.value === 'research') await loadReferences()
  })
}
const referencesLoading = ref(false)
const referenceCandidates = ref<ResearchReferenceCandidate[]>([])
function referenceKey(value: ResearchReference) {
  return JSON.stringify([
    value.kind,
    value.source_id,
    value.account_type || null,
    value.source_date || null
  ])
}
const referenceOptions = computed(() => {
  const values = new Map<string, ResearchReference>()
  for (const value of [...referenceCandidates.value, ...(entry.value?.references || [])])
    values.set(referenceKey(value), value)
  return [...values.values()]
})
async function loadReferences() {
  referenceCandidates.value = []
  if (!workspace.value) return
  referencesLoading.value = true
  try {
    referenceCandidates.value = (
      await stockResearchApi.listReferences({ security_id: workspace.value.security_id })
    ).data.filter(value => value.available)
  } catch {
    ElMessage.warning('引用资料加载失败')
  } finally {
    referencesLoading.value = false
  }
}
function setReferences(keys: string[]) {
  if (!entry.value) return
  entry.value.references = referenceOptions.value
    .filter(value => keys.includes(referenceKey(value)))
    .map(({ kind, source_id, account_type, source_date, label }) => ({
      kind,
      source_id,
      account_type,
      source_date,
      label
    }))
  scheduleEntry()
}
async function entryAction(command: string) {
  await transition(async () => {
    if (!entry.value) return
    if (command === 'convert') {
      if (!editableEntry.value) return
      const target = entry.value.entry_type === 'note' ? 'research' : 'note'
      if (target === 'note' && (!entry.value.title.trim() || !entry.value.body.trim())) {
        ElMessage.warning('转为笔记前请填写标题和正文')
        return
      }
      entry.value = (await stockResearchApi.convertEntry(entry.value.id, target)).data
      section.value = target
      entryPage.value = 1
      entryStatus.value = ''
      if (target === 'research') await loadReferences()
      ElMessage.success(target === 'research' ? '已转为调研' : '已转为笔记')
    } else if (command === 'archive') {
      entry.value = (await stockResearchApi.archiveEntry(entry.value.id)).data
      editorSession.value += 1
      ElMessage.success('已归档')
    } else if (command === 'delete') {
      await ElMessageBox.confirm(
        `将“${entry.value.title || sectionLabel.value}”移入回收站？`,
        '移入回收站',
        { type: 'warning', confirmButtonText: '移入回收站', cancelButtonText: '取消' }
      )
      await stockResearchApi.deleteEntry(entry.value.id)
      entry.value = null
      pendingEditor.value = null
      await loadEntries()
      ElMessage.success('已移入回收站')
    }
  })
}
const versionsVisible = ref(false)
const versionsLoading = ref(false)
const revisions = ref<ResearchRevision[]>([])
const selectedRevision = ref<ResearchRevision | null>(null)
async function loadVersions() {
  if (!workspace.value) return
  versionsLoading.value = true
  try {
    const response = await stockResearchApi.listRevisions(
      entry.value ? 'entry' : 'workspace',
      entry.value?.id || workspace.value.security_id
    )
    revisions.value = response.data
    selectedRevision.value = response.data[0] || null
  } finally {
    versionsLoading.value = false
  }
}
async function openVersions() {
  await transition(async () => {
    revisions.value = []
    selectedRevision.value = null
    versionsVisible.value = true
    await loadVersions()
  })
}
async function saveVersion() {
  await transition(async () => {
    if (!workspace.value) return
    const { value } = await ElMessageBox.prompt('版本标签', '保存版本', {
      inputPlaceholder: '可选',
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValidator: value => !value || value.length <= 100 || '标签不超过 100 字'
    })
    const response = await stockResearchApi.saveWorkspaceVersion(
      workspace.value.security_id,
      value || ''
    )
    workspace.value.current_revision = response.data.revision
    ElMessage.success('版本已保存')
  })
}
async function restoreVersion() {
  await transition(async () => {
    if (!selectedRevision.value || !workspace.value) return
    if (entry.value?.entry_type === 'decision' || entry.value?.status === 'archived') return
    await ElMessageBox.confirm(
      `将版本 ${selectedRevision.value.revision} 恢复为新版本？`,
      '恢复版本',
      { confirmButtonText: '恢复', cancelButtonText: '取消', type: 'warning' }
    )
    await stockResearchApi.restoreRevision(selectedRevision.value.id)
    if (entry.value) {
      entry.value = (await stockResearchApi.getEntry(entry.value.id)).data
      editorSession.value += 1
    } else workspace.value = (await stockResearchApi.getWorkspace(workspace.value.security_id)).data
    await loadVersions()
    ElMessage.success('已恢复为新版本')
  })
}
function snapshotValue(value: unknown): string {
  if (value === undefined || value === null || value === '') return '-'
  if (Array.isArray(value))
    return value.length
      ? value
          .map(item =>
            typeof item === 'object' && item
              ? item.label || item.source_id || JSON.stringify(item)
              : String(item)
          )
          .join('\n')
      : '-'
  return String(value)
}
const trashVisible = ref(false)
const trashLoading = ref(false)
const trashEntries = ref<ResearchEntry[]>([])
const trashTotal = ref(0)
const trashPage = ref(1)
async function loadTrash() {
  trashLoading.value = true
  try {
    const response = await stockResearchApi.listTrash({
      page: trashPage.value,
      page_size: pageSize
    })
    trashEntries.value = response.data.items
    trashTotal.value = response.data.total
    if (!trashEntries.value.length && trashPage.value > 1) {
      trashPage.value -= 1
      await loadTrash()
    }
  } finally {
    trashLoading.value = false
  }
}
async function openTrash() {
  await transition(async () => {
    trashPage.value = 1
    trashVisible.value = true
    await loadTrash()
  })
}
async function changeTrashPage(value: number) {
  await transition(async () => {
    trashPage.value = value
    await loadTrash()
  })
}
async function restoreTrash(item: ResearchEntry) {
  await transition(async () => {
    await stockResearchApi.restoreTrashEntry(item.id)
    await loadTrash()
    if (section.value !== 'thesis' && !entry.value) await loadEntries()
    ElMessage.success('记录已恢复')
  })
}
async function permanentlyDelete(item: ResearchEntry) {
  await transition(async () => {
    await ElMessageBox.confirm(
      `永久删除“${item.title || entryLabels[item.entry_type]}”？此操作无法恢复。`,
      '永久删除',
      { confirmButtonText: '永久删除', cancelButtonText: '取消', type: 'warning' }
    )
    await stockResearchApi.deleteTrashEntry(item.id)
    await loadTrash()
    ElMessage.success('已永久删除')
  })
}
async function guardNavigation() {
  if (busy.value) return false
  busy.value = true
  try {
    return await flushChanges()
  } finally {
    busy.value = false
  }
}
onBeforeRouteLeave(guardNavigation)
onBeforeRouteUpdate(guardNavigation)
function beforeUnload(event: BeforeUnloadEvent) {
  if (autosave.state.value !== 'saved' || formalEditor.value?.dirty) {
    event.preventDefault()
    event.returnValue = ''
  }
}
window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => {
  ++workspaceRequest
  ++entriesRequest
  window.removeEventListener('beforeunload', beforeUnload)
})
watch(() => [route.params.code, route.query.market], loadWorkspace, { immediate: true })
</script>

<style scoped>
.research-workspace {
  width: 100%;
  min-width: 0;
  letter-spacing: 0;
}
.workspace-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--el-border-color-light);
}
.security-heading {
  min-width: 0;
  overflow-wrap: anywhere;
}
.security-heading h1 {
  margin: 0 0 5px;
  font-size: 23px;
  font-weight: 600;
}
.security-heading span,
.muted {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.header-actions,
.document-actions,
.document-title,
.trash-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.header-actions {
  flex-wrap: wrap;
}
.header-actions .el-button + .el-button,
.trash-actions .el-button + .el-button {
  margin-left: 0;
}
.dropdown-icon {
  margin-left: 8px;
}
.market-prompt {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 32px 0;
}
.market-prompt .el-select {
  width: 180px;
}
.workspace-layout {
  display: grid;
  grid-template-columns: 164px minmax(0, 1fr);
  gap: 32px;
}
.workspace-nav {
  padding-top: 22px;
  border-right: 1px solid var(--el-border-color-light);
}
.workspace-nav button {
  display: flex;
  align-items: center;
  gap: 10px;
  width: calc(100% - 16px);
  min-height: 42px;
  padding: 10px 12px;
  margin-bottom: 4px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--el-text-color-regular);
  cursor: pointer;
  font-size: 14px;
}
.workspace-nav button.active {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  font-weight: 600;
}
.workspace-nav button:hover {
  background: var(--el-fill-color-light);
}
.workspace-nav .trash-nav {
  margin-top: 28px;
}
.workspace-document {
  min-width: 0;
  padding-top: 22px;
  padding-bottom: 40px;
}
.document-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 16px;
  min-height: 34px;
}
.document-title {
  min-width: 0;
  flex-wrap: wrap;
}
h2 {
  font-size: 18px;
  margin: 0;
}
.document-fields {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
}
.thesis-details {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 22px 28px;
  padding-top: 24px;
  border-top: 1px solid var(--el-border-color-light);
}
.thesis-field {
  min-width: 0;
}
.field-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 32px;
}
.field-heading h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
}
.list-field-row {
  display: flex;
  gap: 4px;
  margin-top: 8px;
}
.list-field-row .el-input {
  min-width: 0;
}
.research-fields {
  margin-top: 24px;
}
.reference-select {
  width: 100%;
}
.readonly-title {
  margin: 0;
  font-size: 17px;
  overflow-wrap: anywhere;
}
.versions-layout {
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr);
  gap: 24px;
  min-height: 280px;
  max-height: 65vh;
  overflow: auto;
}
.revision-list {
  border-right: 1px solid var(--el-border-color-light);
  padding-right: 12px;
}
.revision-list button {
  display: flex;
  flex-direction: column;
  gap: 5px;
  width: 100%;
  padding: 12px;
  border: 0;
  border-bottom: 1px solid var(--el-border-color-light);
  text-align: left;
  background: transparent;
  color: var(--el-text-color-primary);
  cursor: pointer;
  overflow-wrap: anywhere;
}
.revision-list button.selected {
  background: var(--el-fill-color-light);
}
.revision-list small {
  color: var(--el-text-color-secondary);
}
.revision-document {
  min-width: 0;
}
.revision-document h3 {
  font-size: 17px;
  margin-top: 0;
  overflow-wrap: anywhere;
}
.snapshot-fields {
  display: grid;
  grid-template-columns: 100px minmax(0, 1fr);
  gap: 12px;
  margin: 16px 0;
}
.snapshot-fields dt {
  color: var(--el-text-color-secondary);
}
.snapshot-fields dd {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.trash-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 0;
  border-bottom: 1px solid var(--el-border-color-light);
}
.trash-row > div:first-child {
  min-width: 0;
  overflow-wrap: anywhere;
}
.trash-row p {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.trash-actions {
  flex-shrink: 0;
}
@media (max-width: 760px) {
  .workspace-header {
    align-items: flex-start;
    flex-direction: column;
  }
  .header-actions {
    width: 100%;
  }
  .workspace-layout {
    grid-template-columns: minmax(0, 1fr);
    gap: 0;
  }
  .workspace-nav {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    padding-top: 12px;
    padding-bottom: 12px;
    border-right: 0;
    border-bottom: 1px solid var(--el-border-color-light);
  }
  .workspace-nav button {
    width: auto;
    margin: 0;
    padding: 8px;
    gap: 5px;
  }
  .workspace-nav .trash-nav {
    margin-top: 0;
  }
  .workspace-document {
    padding-top: 16px;
  }
  .thesis-details {
    grid-template-columns: minmax(0, 1fr);
  }
  .versions-layout {
    grid-template-columns: minmax(0, 1fr);
    gap: 16px;
  }
  .revision-list {
    display: flex;
    overflow-x: auto;
    border-right: 0;
    padding: 0;
  }
  .revision-list button {
    flex: 0 0 150px;
  }
  .trash-row {
    align-items: flex-start;
    flex-direction: column;
  }
  .trash-actions {
    flex-wrap: wrap;
  }
}
</style>
