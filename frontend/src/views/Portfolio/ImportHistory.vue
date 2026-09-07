<template>
  <section class="portfolio-page" aria-labelledby="imports-title">
    <header class="page-header">
      <h1 id="imports-title">导入记录</h1>
      <div class="toolbar">
        <el-tooltip content="刷新"
          ><el-button :icon="Refresh" :loading="loading" aria-label="刷新" @click="loadImports"
        /></el-tooltip>
        <el-button type="primary" :icon="Upload" @click="dialogOpen = true">导入文件</el-button>
      </div>
    </header>
    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error"
      ><el-button :icon="Refresh" :loading="loading" @click="loadImports">重试</el-button></el-alert
    >
    <el-table v-loading="loading" :data="imports" height="460" stripe empty-text="暂无数据">
      <el-table-column label="完成时间" width="180"
        ><template #default="{ row }">{{
          completedTime(row.completed_at)
        }}</template></el-table-column
      >
      <el-table-column label="来源类型" width="110"
        ><template #default="{ row }">{{
          row.source_type === 'snapshot' ? '持仓快照' : '交割单'
        }}</template></el-table-column
      >
      <el-table-column
        prop="source_filename"
        label="文件名"
        min-width="240"
        show-overflow-tooltip
      />
      <el-table-column prop="file_sha256_short" label="文件指纹" width="150" />
      <el-table-column prop="row_count" label="行数" width="90" align="right" />
      <el-table-column prop="warning_count" label="警告数" width="90" align="right" />
      <el-table-column label="覆盖区间 / 快照日期" width="240"
        ><template #default="{ row }">{{
          row.source_type === 'snapshot'
            ? formatDate(row.observed_on)
            : `${formatDate(row.coverage_from)} ~ ${formatDate(row.coverage_through)}`
        }}</template></el-table-column
      >
      <el-table-column label="状态" width="100"
        ><template #default="{ row }"
          ><el-tag
            :type="
              row.status === 'imported' ? 'success' : row.status === 'failed' ? 'danger' : 'info'
            "
            size="small"
            >{{ statusLabels[row.status as ImportHistoryItem['status']] }}</el-tag
          ></template
        ></el-table-column
      >
    </el-table>
    <div class="pagination">
      <el-pagination
        v-model:current-page="page"
        :disabled="loading"
        :page-size="pageSize"
        :total="total"
        :pager-count="5"
        layout="prev, pager, next"
        @current-change="loadImports"
      /><el-select v-model="pageSize" aria-label="每页条数" @change="changePageSize"
        ><el-option
          v-for="size in [20, 50, 100]"
          :key="size"
          :value="size"
          :label="`${size} 条/页`" /></el-select
      ><span class="total">共 {{ total }} 条</span>
    </div>
    <el-dialog
      v-model="dialogOpen"
      title="导入文件"
      width="min(480px, calc(100vw - 32px))"
      :close-on-click-modal="false"
      :close-on-press-escape="!importing"
      :show-close="!importing"
      destroy-on-close
      :before-close="cancelImport"
    >
      <el-form label-position="top" @submit.prevent="submitImport">
        <el-form-item label="券商文件">
          <el-upload
            ref="upload"
            :auto-upload="false"
            :show-file-list="false"
            :disabled="importing"
            :on-change="selectFile"
          >
            <el-button :icon="Upload" :disabled="importing">{{
              selectedFile ? '更换文件' : '选择文件'
            }}</el-button>
          </el-upload>
          <div v-if="selectedFile" class="filename">{{ selectedFile.name }}</div>
        </el-form-item>
        <el-form-item v-if="needsSnapshotDate" label="快照日期" required>
          <el-date-picker
            ref="snapshotDateInput"
            v-model="snapshotDate"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="快照日期"
            aria-label="快照日期"
            :disabled="importing"
          />
        </el-form-item>
        <el-alert
          v-if="importError"
          type="error"
          :closable="false"
          show-icon
          :title="importError"
        />
      </el-form>
      <template #footer>
        <el-button :disabled="importing" @click="cancelImport">取消</el-button>
        <el-button
          type="primary"
          :icon="Upload"
          :loading="importing"
          :disabled="!selectedFile || (needsSnapshotDate && !snapshotDate)"
          @click="submitImport"
          >确认导入</el-button
        >
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref, shallowRef } from 'vue'
import { ElMessage, type UploadFile, type UploadInstance } from 'element-plus'
import { Refresh, Upload } from '@element-plus/icons-vue'
import {
  getPortfolioErrorDetail,
  realPortfolioApi,
  type ImportHistoryItem
} from '@/api/realPortfolio'
import { formatDate } from './portfolioFormatters'

const imports = ref<ImportHistoryItem[]>([])
const loading = ref(false)
const error = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const statusLabels = { publishing: '导入中', imported: '已导入', failed: '失败' }
const dialogOpen = ref(false)
const importing = ref(false)
const selectedFile = shallowRef<File | null>(null)
const upload = ref<UploadInstance>()
const snapshotDate = ref<string>()
const snapshotDateInput = ref<{ focus: () => void }>()
const needsSnapshotDate = ref(false)
const importError = ref('')
let requestId = 0

function completedTime(value: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString('zh-CN', { hour12: false })
}

function selectFile(file: UploadFile) {
  if (!file.raw) return
  selectedFile.value = file.raw
  snapshotDate.value = undefined
  needsSnapshotDate.value = false
  importError.value = ''
  upload.value?.clearFiles()
}

function cancelImport() {
  if (importing.value) return
  dialogOpen.value = false
  selectedFile.value = null
  snapshotDate.value = undefined
  needsSnapshotDate.value = false
  importError.value = ''
  upload.value?.clearFiles()
}

function changePageSize() {
  page.value = 1
  loadImports()
}

async function loadImports() {
  const id = ++requestId
  loading.value = true
  error.value = ''
  try {
    const response = await realPortfolioApi.getImports(page.value, pageSize.value)
    if (id === requestId) {
      imports.value = response.data.items
      total.value = response.data.total
    }
  } catch (cause: unknown) {
    if (id === requestId) {
      imports.value = []
      total.value = 0
      error.value = getPortfolioErrorDetail(cause)?.message || '导入记录加载失败'
    }
  } finally {
    if (id === requestId) loading.value = false
  }
}

async function submitImport() {
  if (!selectedFile.value || importing.value || (needsSnapshotDate.value && !snapshotDate.value))
    return
  importing.value = true
  importError.value = ''
  try {
    const response = await realPortfolioApi.importFile(selectedFile.value, {
      asOf: snapshotDate.value,
      dryRun: false
    })
    ElMessage.success(response.data.status === 'duplicate' ? '文件已导入' : '导入完成')
    importing.value = false
    cancelImport()
    page.value = 1
    await loadImports()
  } catch (cause: unknown) {
    const detail = getPortfolioErrorDetail(cause)
    importError.value = detail?.message || '导入失败'
    if (detail?.code === 'SNAPSHOT_DATE_REQUIRED') {
      needsSnapshotDate.value = true
      importing.value = false
      await nextTick()
      snapshotDateInput.value?.focus()
    } else if (detail?.code === 'SNAPSHOT_DATE_CONFLICT' && detail.observed_on) {
      importError.value = `${detail.message}。原快照日期：${formatDate(detail.observed_on)}`
    }
  } finally {
    importing.value = false
  }
}

onMounted(loadImports)
</script>

<style scoped lang="scss">
:global(.basic-layout:has(.portfolio-page) > .main-container > .footer) {
  height: auto;
  min-height: 60px;
  flex-shrink: 0;
}
.portfolio-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
  width: 100%;
}
.page-header,
.toolbar,
.pagination {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.page-header {
  justify-content: space-between;
}
h1 {
  margin: 0;
  font-size: 20px;
  line-height: 28px;
}
.toolbar .el-button {
  margin: 0;
}
.toolbar .el-button:first-child {
  width: 32px;
  height: 32px;
}
.pagination {
  justify-content: flex-end;
}
.pagination .el-select {
  width: 115px;
}
.total {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.filename {
  width: 100%;
  margin-top: 8px;
  overflow-wrap: anywhere;
}
:deep(.el-alert__content) {
  min-width: 0;
  overflow-wrap: anywhere;
}
:deep(.el-table .cell) {
  word-break: normal;
}
:deep(.el-form .el-date-editor) {
  width: 100%;
}
@media (max-width: 767px) {
  .pagination {
    justify-content: flex-start;
  }
}
</style>
