<template>
  <section class="research-directory">
    <header class="directory-header">
      <div>
        <h1>研究与复盘</h1>
        <p>按证券整理当前论点、研究记录与持仓关联。</p>
      </div>
      <div class="header-actions">
        <el-button @click="reviewDialogVisible = true">
          <el-icon><EditPen /></el-icon>
          新建例行复盘
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

    <el-dialog v-model="reviewDialogVisible" title="新建例行复盘" width="min(480px, 92vw)">
      <el-form label-position="top">
        <el-form-item label="复盘范围">
          <el-checkbox v-model="reviewScope.include_market">市场概况</el-checkbox>
          <el-checkbox v-model="reviewScope.include_real_holdings">真实持仓</el-checkbox>
          <el-checkbox v-model="reviewScope.include_paper_holdings">模拟持仓</el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reviewDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creatingReview" @click="createRoutineReview"
          >创建草稿</el-button
        >
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useDebounceFn } from '@vueuse/core'
import { ElMessage } from 'element-plus'
import { ArrowRight, EditPen, Plus, Refresh, Search, StarFilled } from '@element-plus/icons-vue'
import { formatDateTime } from '@/utils/datetime'
import {
  stockResearchApi,
  type ResearchEntryType,
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
const creatingReview = ref(false)
const workspaceForm = reactive({ market: 'CN' as ResearchMarket, code: '', name: '' })
const reviewScope = reactive({
  include_market: true,
  include_real_holdings: true,
  include_paper_holdings: false
})

const marketLabel: Record<ResearchMarket, string> = { CN: 'A 股', HK: '港股', US: '美股' }
const entryTypeLabel: Record<ResearchEntryType, string> = {
  note: '笔记',
  research: '调研',
  decision: '决策',
  review: '复盘'
}

async function loadWorkspaces() {
  loading.value = true
  try {
    const response = await stockResearchApi.listWorkspaces({
      market: market.value || undefined,
      query: searchText.value.trim() || undefined,
      real_holding: realHoldingOnly.value || undefined,
      paper_holding: paperHoldingOnly.value || undefined,
      watchlisted: watchlistedOnly.value || undefined,
      page: page.value,
      page_size: pageSize.value
    })
    workspaces.value = response.data.items
    total.value = response.data.total
  } catch {
    workspaces.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
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

async function createRoutineReview() {
  creatingReview.value = true
  try {
    await stockResearchApi.createEntry({
      entry_type: 'review',
      scope: 'portfolio',
      title: `例行复盘 ${new Date().toLocaleDateString('zh-CN')}`,
      review_kind: 'routine',
      scope_metadata: { ...reviewScope }
    })
    reviewDialogVisible.value = false
    ElMessage.success('例行复盘草稿已创建')
  } finally {
    creatingReview.value = false
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

onMounted(loadWorkspaces)
</script>

<style scoped lang="scss">
.research-directory {
  width: 100%;
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
