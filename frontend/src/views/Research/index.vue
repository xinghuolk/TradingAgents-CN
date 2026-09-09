<template>
  <section class="research-directory">
    <header class="directory-header">
      <div>
        <h1>研究与复盘</h1>
        <p>按证券整理当前论点、研究记录与持仓关联。</p>
      </div>
      <div class="header-actions">
        <el-button :icon="Delete" @click="openTrash">回收站</el-button>
        <el-button @click="openReview()">
          <el-icon><EditPen /></el-icon>
          新建复盘
        </el-button>
        <el-button type="primary" @click="workspaceDialogVisible = true">
          <el-icon><Plus /></el-icon>
          创建/进入工作区
        </el-button>
      </div>
    </header>

    <div class="directory-toolbar">
      <el-input
        v-model="searchText"
        class="search-input"
        clearable
        placeholder="搜索股票代码或名称"
        @input="searchWorkspaces"
        @clear="loadWorkspaces"
      >
        <template #prefix
          ><el-icon><Search /></el-icon
        ></template>
      </el-input>
      <el-select
        v-model="market"
        class="market-select"
        placeholder="全部市场"
        clearable
        @change="resetAndLoad"
      >
        <el-option label="A 股" value="CN" />
        <el-option label="港股" value="HK" />
        <el-option label="美股" value="US" />
      </el-select>
      <div class="flag-filters" aria-label="工作区筛选">
        <el-checkbox v-model="realHoldingOnly" @change="resetAndLoad">真实持仓</el-checkbox>
        <el-checkbox v-model="paperHoldingOnly" @change="resetAndLoad">模拟持仓</el-checkbox>
        <el-checkbox v-model="watchlistedOnly" @change="resetAndLoad">我的自选</el-checkbox>
      </div>
      <el-button :loading="loading" circle title="刷新" aria-label="刷新" @click="loadWorkspaces">
        <el-icon><Refresh /></el-icon>
      </el-button>
    </div>

    <div class="directory-table">
      <el-table
        v-loading="loading"
        :data="workspaces"
        row-class-name="workspace-row"
        empty-text="暂无研究工作区"
        @row-click="openWorkspace"
      >
        <el-table-column label="证券" min-width="190">
          <template #default="{ row }">
            <div class="security-cell">
              <strong>{{ row.name }}</strong>
              <span>{{ row.code }} · {{ marketLabel[row.market] }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="当前论点" min-width="280" show-overflow-tooltip>
          <template #default="{ row }">
            <span :class="{ muted: !row.thesis_summary }">{{
              row.thesis_summary || '尚未填写'
            }}</span>
          </template>
        </el-table-column>
        <el-table-column label="关联状态" min-width="210">
          <template #default="{ row }">
            <div class="status-tags">
              <el-tag v-if="row.has_real_holding" type="success" effect="plain" size="small"
                >真实持仓</el-tag
              >
              <el-tag v-if="row.has_paper_holding" type="warning" effect="plain" size="small"
                >模拟持仓</el-tag
              >
              <el-tag v-if="row.watchlisted" type="info" effect="plain" size="small">
                <el-icon><StarFilled /></el-icon>
                我的自选
              </el-tag>
              <span
                v-if="!row.has_real_holding && !row.has_paper_holding && !row.watchlisted"
                class="muted"
                >-</span
              >
            </div>
          </template>
        </el-table-column>
        <el-table-column label="最近记录" min-width="160">
          <template #default="{ row }">
            <div v-if="row.latest_entry_type" class="date-cell">
              <span>{{ entryTypeLabel[row.latest_entry_type] }}</span>
              <small>{{ formatCompactDate(row.latest_entry_at) }}</small>
            </div>
            <span v-else class="muted">暂无记录</span>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" min-width="150">
          <template #default="{ row }">{{ formatCompactDate(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column width="52" align="right">
          <template #default
            ><el-icon class="row-arrow"><ArrowRight /></el-icon
          ></template>
        </el-table-column>
      </el-table>
    </div>

    <el-pagination
      v-if="total > pageSize"
      v-model:current-page="page"
      v-model:page-size="pageSize"
      class="directory-pagination"
      background
      layout="prev, pager, next, total"
      :total="total"
      @current-change="loadWorkspaces"
    />

    <el-dialog
      v-model="workspaceDialogVisible"
      title="创建或进入研究工作区"
      width="min(480px, 92vw)"
    >
      <el-form label-position="top" @submit.prevent="createWorkspace">
        <div class="workspace-form-grid">
          <el-form-item label="市场" required>
            <el-select v-model="workspaceForm.market">
              <el-option label="A 股" value="CN" />
              <el-option label="港股" value="HK" />
              <el-option label="美股" value="US" />
            </el-select>
          </el-form-item>
          <el-form-item label="股票代码" required>
            <el-input v-model="workspaceForm.code" autocomplete="off" />
          </el-form-item>
        </div>
        <el-form-item label="股票名称" required>
          <el-input v-model="workspaceForm.name" autocomplete="off" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="workspaceDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creatingWorkspace" @click="createWorkspace"
          >确定</el-button
        >
      </template>
    </el-dialog>

    <section class="portfolio-reviews">
      <h2>组合复盘</h2>
      <ResearchEntryList
        :entries="reviews"
        :loading="reviewsLoading"
        :error="reviewsError"
        :disabled="false"
        :total="reviewsTotal"
        :page="reviewsPage"
        :page-size="20"
        :status="reviewsStatus"
        @select="openReview"
        @retry="loadReviews"
        @page="changeReviewPage"
        @status="changeReviewStatus"
      />
    </section>
    <el-dialog
      v-model="reviewDialogVisible"
      :title="activeReview ? '复盘' : '新建复盘'"
      width="min(920px, 96vw)"
      :close-on-click-modal="false"
      :before-close="closeReview"
      destroy-on-close
    >
      <ReviewEditor
        v-if="reviewDialogVisible"
        ref="reviewEditor"
        :entry="activeReview"
        @saved="reviewSaved"
      />
      <template #footer>
        <el-button
          v-if="activeReview && activeReview.status !== 'archived'"
          :icon="FolderOpened"
          :loading="lifecycleBusy"
          @click="archiveReview"
          >归档</el-button
        >
        <el-button v-if="activeReview" :icon="Delete" :loading="lifecycleBusy" @click="deleteReview"
          >移入回收站</el-button
        >
      </template>
    </el-dialog>
    <el-dialog v-model="trashVisible" title="研究回收站" width="min(760px, 94vw)">
      <el-empty v-if="!trashEntries.length" description="回收站为空" />
      <div v-for="item in trashEntries" :key="item.id" class="trash-row">
        <span
          >{{ item.title || (item.entry_type === 'review' ? '复盘' : item.entry_type) }} ·
          {{ item.security_id || '组合' }}</span
        >
        <div class="header-actions">
          <el-button :icon="Refresh" :disabled="lifecycleBusy" @click="restoreTrash(item)"
            >恢复</el-button
          >
          <el-button
            :icon="Delete"
            type="danger"
            plain
            :disabled="lifecycleBusy"
            @click="permanentlyDeleteTrash(item)"
            >永久删除</el-button
          >
        </div>
      </div>
      <el-pagination
        v-if="trashTotal > 20"
        v-model:current-page="trashPage"
        :page-size="20"
        :total="trashTotal"
        layout="prev, pager, next"
        @current-change="loadTrash"
      />
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { useDebounceFn } from '@vueuse/core'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowRight,
  Delete,
  EditPen,
  FolderOpened,
  Plus,
  Refresh,
  Search,
  StarFilled
} from '@element-plus/icons-vue'
import { formatDateTime } from '@/utils/datetime'
import { createLatestRequestCoordinator } from '@/utils/latestRequest'
import ReviewEditor from '@/components/Research/ReviewEditor.vue'
import ResearchEntryList from '@/components/Research/ResearchEntryList.vue'
import {
  stockResearchApi,
  type ResearchEntryType,
  type ResearchEntry,
  type ResearchEntryStatus,
  type ResearchMarket,
  type ResearchWorkspaceSummary
} from '@/api/stockResearch'

const router = useRouter()
const loading = ref(false)
const workspaces = ref<ResearchWorkspaceSummary[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const searchText = ref('')
const market = ref<ResearchMarket | ''>('')
const realHoldingOnly = ref(false)
const paperHoldingOnly = ref(false)
const watchlistedOnly = ref(false)

const workspaceDialogVisible = ref(false)
const reviewDialogVisible = ref(false)
const creatingWorkspace = ref(false)
const workspaceForm = reactive({ market: 'CN' as ResearchMarket, code: '', name: '' })
const activeReview = ref<ResearchEntry | null>(null)
const reviewEditor = ref<{ flush: () => Promise<boolean>; dirty: boolean } | null>(null)
const reviews = ref<ResearchEntry[]>([])
const reviewsTotal = ref(0)
const reviewsPage = ref(1)
const reviewsStatus = ref<ResearchEntryStatus | ''>('')
const reviewsLoading = ref(false)
const reviewsError = ref(false)
const lifecycleBusy = ref(false)
const trashVisible = ref(false)
const trashEntries = ref<ResearchEntry[]>([])
const trashTotal = ref(0)
const trashPage = ref(1)
async function archiveReview() {
  if (lifecycleBusy.value || !activeReview.value || activeReview.value.status === 'archived') return
  if (reviewEditor.value && !(await reviewEditor.value.flush())) return
  lifecycleBusy.value = true
  try {
    await stockResearchApi.archiveEntry(activeReview.value.id)
    reviewDialogVisible.value = false
    await loadReviews()
  } catch {
    ElMessage.error('归档失败')
  } finally {
    lifecycleBusy.value = false
  }
}
async function deleteReview() {
  if (lifecycleBusy.value || !activeReview.value) return
  if (reviewEditor.value && !(await reviewEditor.value.flush())) return
  lifecycleBusy.value = true
  try {
    await stockResearchApi.deleteEntry(activeReview.value.id)
    reviewDialogVisible.value = false
    await loadReviews()
  } catch {
    ElMessage.error('删除失败')
  } finally {
    lifecycleBusy.value = false
  }
}
async function loadTrash() {
  const response = await stockResearchApi.listTrash({ page: trashPage.value, page_size: 20 })
  trashEntries.value = response.data.items
  trashTotal.value = response.data.total
  if (!trashEntries.value.length && trashPage.value > 1) {
    trashPage.value--
    await loadTrash()
  }
}
async function openTrash() {
  try {
    trashPage.value = 1
    await loadTrash()
    trashVisible.value = true
  } catch {
    ElMessage.error('回收站加载失败')
  }
}
async function restoreTrash(item: ResearchEntry) {
  if (lifecycleBusy.value) return
  lifecycleBusy.value = true
  try {
    await stockResearchApi.restoreTrashEntry(item.id)
    await loadTrash()
    await loadReviews()
  } catch {
    ElMessage.error('恢复失败')
  } finally {
    lifecycleBusy.value = false
  }
}
async function permanentlyDeleteTrash(item: ResearchEntry) {
  if (lifecycleBusy.value) return
  try {
    await ElMessageBox.confirm('永久删除此研究记录？', '永久删除', {
      type: 'warning',
      confirmButtonText: '永久删除',
      cancelButtonText: '取消'
    })
    lifecycleBusy.value = true
    await stockResearchApi.deleteTrashEntry(item.id)
    await loadTrash()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error('永久删除失败')
  } finally {
    lifecycleBusy.value = false
  }
}
const coordinateReviewRequest = createLatestRequestCoordinator()
async function loadReviews() {
  reviewsLoading.value = true
  await coordinateReviewRequest(
    () =>
      stockResearchApi.listEntries({
        entry_type: 'review',
        scope: 'portfolio',
        status: reviewsStatus.value || undefined,
        page: reviewsPage.value,
        page_size: 20
      }),
    {
      onSuccess(response) {
        reviews.value = response.data.items
        reviewsTotal.value = response.data.total
        reviewsError.value = false
      },
      onError() {
        reviewsError.value = true
      },
      onSettled() {
        reviewsLoading.value = false
      }
    }
  )
}
function changeReviewPage(value: number) {
  reviewsPage.value = value
  void loadReviews()
}
function changeReviewStatus(value: ResearchEntryStatus | '') {
  reviewsStatus.value = value
  reviewsPage.value = 1
  void loadReviews()
}
async function openReview(entry?: ResearchEntry) {
  try {
    activeReview.value = entry ? (await stockResearchApi.getEntry(entry.id)).data : null
    reviewDialogVisible.value = true
  } catch {
    ElMessage.error('复盘加载失败')
  }
}
function reviewSaved(entry: ResearchEntry) {
  activeReview.value = entry
  void loadReviews()
}
async function closeReview(done: () => void) {
  if (reviewEditor.value && !(await reviewEditor.value.flush())) return
  done()
  await loadReviews()
}
onBeforeRouteLeave(async () => !reviewEditor.value || (await reviewEditor.value.flush()))
function beforeUnload(event: BeforeUnloadEvent) {
  if (reviewEditor.value?.dirty) {
    event.preventDefault()
    event.returnValue = ''
  }
}
window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))

const marketLabel: Record<ResearchMarket, string> = { CN: 'A 股', HK: '港股', US: '美股' }
const entryTypeLabel: Record<ResearchEntryType, string> = {
  note: '笔记',
  research: '调研',
  decision: '决策',
  review: '复盘'
}
const coordinateWorkspaceRequest = createLatestRequestCoordinator()

async function loadWorkspaces() {
  loading.value = true
  await coordinateWorkspaceRequest(
    () =>
      stockResearchApi.listWorkspaces({
        market: market.value || undefined,
        query: searchText.value.trim() || undefined,
        real_holding: realHoldingOnly.value || undefined,
        paper_holding: paperHoldingOnly.value || undefined,
        watchlisted: watchlistedOnly.value || undefined,
        page: page.value,
        page_size: pageSize.value
      }),
    {
      onSuccess(response) {
        workspaces.value = response.data.items
        total.value = response.data.total
      },
      onError() {
        workspaces.value = []
        total.value = 0
      },
      onSettled() {
        loading.value = false
      }
    }
  )
}

const searchWorkspaces = useDebounceFn(() => {
  page.value = 1
  void loadWorkspaces()
}, 300)

function resetAndLoad() {
  page.value = 1
  void loadWorkspaces()
}

function openWorkspace(item: ResearchWorkspaceSummary) {
  void router.push({
    name: 'ResearchWorkspace',
    params: { code: item.code },
    query: { market: item.market }
  })
}

async function createWorkspace() {
  const code = workspaceForm.code.trim().toUpperCase()
  const name = workspaceForm.name.trim()
  if (!code || !name) {
    ElMessage.warning('请填写股票代码和名称')
    return
  }
  creatingWorkspace.value = true
  try {
    const response = await stockResearchApi.createWorkspace({
      market: workspaceForm.market,
      code,
      name
    })
    workspaceDialogVisible.value = false
    await router.push({
      name: 'ResearchWorkspace',
      params: { code: response.data.code },
      query: { market: response.data.market }
    })
  } finally {
    creatingWorkspace.value = false
  }
}

function formatCompactDate(value: string | null) {
  return formatDateTime(value, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

onMounted(() => {
  void loadWorkspaces()
  void loadReviews()
})
</script>

<style scoped lang="scss">
.research-directory {
  width: 100%;
}
.portfolio-reviews {
  margin-top: 32px;
  border-top: 1px solid var(--el-border-color-light);
  padding-top: 20px;
}
.portfolio-reviews h2 {
  font-size: 18px;
  margin: 0 0 16px;
}
.trash-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-light);
}
.trash-row span {
  overflow-wrap: anywhere;
}

.directory-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 20px;

  h1 {
    margin: 0 0 6px;
    font-size: 24px;
    font-weight: 600;
    letter-spacing: 0;
  }

  p {
    margin: 0;
    color: var(--el-text-color-secondary);
  }
}

.header-actions,
.directory-toolbar,
.flag-filters,
.status-tags {
  display: flex;
  align-items: center;
}

.header-actions,
.status-tags {
  gap: 8px;
}

.directory-toolbar {
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid var(--el-border-color-lighter);
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.search-input {
  width: min(340px, 100%);
}

.market-select {
  width: 130px;
}

.flag-filters {
  flex: 1;
  gap: 16px;
  min-width: 300px;
}

.directory-table {
  width: 100%;
  overflow-x: auto;

  :deep(.el-table) {
    min-width: 980px;
  }

  :deep(.workspace-row) {
    cursor: pointer;
  }
}

.security-cell,
.date-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.security-cell span,
.date-cell small,
.muted {
  color: var(--el-text-color-secondary);
}

.status-tags {
  flex-wrap: wrap;
}

.status-tags .el-tag {
  gap: 3px;
}

.row-arrow {
  color: var(--el-text-color-placeholder);
}

.directory-pagination {
  justify-content: flex-end;
  margin-top: 18px;
}

.workspace-form-grid {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: 12px;
}

@media (max-width: 760px) {
  .directory-header {
    flex-direction: column;
  }

  .header-actions {
    width: 100%;
    flex-wrap: wrap;
  }

  .directory-toolbar {
    align-items: stretch;
    flex-wrap: wrap;
  }

  .search-input,
  .market-select {
    width: 100%;
  }

  .flag-filters {
    min-width: 0;
    width: 100%;
    flex-wrap: wrap;
  }

  .workspace-form-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>
