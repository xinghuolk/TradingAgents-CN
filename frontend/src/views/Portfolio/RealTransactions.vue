<template>
  <section class="portfolio-page" aria-labelledby="transactions-title">
    <header class="page-header">
      <h1 id="transactions-title">真实成交记录</h1>
      <el-tooltip content="刷新"
        ><el-button :icon="Refresh" :loading="loading" aria-label="刷新" @click="loadTrades"
      /></el-tooltip>
    </header>
    <div class="toolbar">
      <el-date-picker
        v-model="dateRange"
        class="date-range"
        type="daterange"
        value-format="YYYY-MM-DD"
        start-placeholder="开始日期"
        end-placeholder="结束日期"
        aria-label="成交日期范围"
        @change="applyFilters"
      />
      <el-select
        v-model="market"
        aria-label="市场"
        placeholder="全部市场"
        clearable
        @change="applyFilters"
        ><el-option label="A股" value="CN" /><el-option label="港股" value="HK"
      /></el-select>
      <el-select
        v-model="eventType"
        aria-label="事件类型"
        placeholder="全部事件"
        clearable
        @change="applyFilters"
        ><el-option
          v-for="option in eventTypes"
          :key="option.value"
          :label="option.label"
          :value="option.value"
      /></el-select>
      <el-select
        v-model="completeness"
        aria-label="完整性"
        placeholder="全部完整性"
        clearable
        @change="applyFilters"
        ><el-option
          v-for="value in completenessOptions"
          :key="value"
          :label="completenessLabel(value)"
          :value="value"
      /></el-select>
      <el-tooltip content="清除筛选"
        ><el-button :icon="Filter" aria-label="清除筛选" @click="clearFilters"
      /></el-tooltip>
    </div>
    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error"
      ><el-button :icon="Refresh" :loading="loading" @click="loadTrades">重试</el-button></el-alert
    >
    <el-table
      v-loading="loading"
      :data="trades"
      row-key="id"
      height="460"
      stripe
      empty-text="暂无数据"
    >
      <el-table-column label="操作日期" width="120"
        ><template #default="{ row }">{{
          formatDate(row.trade_date || row.settlement_date)
        }}</template></el-table-column
      >
      <el-table-column label="市场" width="80"
        ><template #default="{ row }">{{ marketLabel(row.market) }}</template></el-table-column
      >
      <el-table-column prop="code" label="证券代码" width="110"
        ><template #default="{ row }">{{ row.code || '-' }}</template></el-table-column
      >
      <el-table-column prop="name" label="证券名称" min-width="150"
        ><template #default="{ row }">{{ row.name || '-' }}</template></el-table-column
      >
      <el-table-column prop="operation_label" label="操作" min-width="180" show-overflow-tooltip />
      <el-table-column label="数量变动" width="140" align="right"
        ><template #default="{ row }">{{
          signedQuantity(row.security_quantity)
        }}</template></el-table-column
      >
      <el-table-column label="人民币交收金额" width="180" align="right"
        ><template #default="{ row }"
          ><template v-if="cnyMovements(row).length"
            ><div v-for="(movement, index) in cnyMovements(row)" :key="index">
              {{ formatMoney(movement.amount, 'CNY') }}
            </div></template
          ><span v-else>-</span></template
        ></el-table-column
      >
      <el-table-column label="交易币种" width="100"
        ><template #default="{ row }">{{ row.trade_currency || '-' }}</template></el-table-column
      >
      <el-table-column label="完整性" width="105"
        ><template #default="{ row }"
          ><el-tag :type="row.completeness === 'complete' ? 'success' : 'warning'" size="small">{{
            completenessLabel(row.completeness)
          }}</el-tag></template
        ></el-table-column
      >
      <el-table-column label="警告" min-width="250"
        ><template #default="{ row }"
          ><div v-for="(warning, index) in row.warnings" :key="index" class="warning-line">
            {{ warningType(warning.type) }}：{{ warning.message }}
          </div>
          <span v-if="!row.warnings.length">-</span></template
        ></el-table-column
      >
    </el-table>
    <div class="pagination">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :disabled="loading"
        :total="total"
        :page-sizes="[20, 50, 100]"
        :pager-count="5"
        layout="prev, pager, next"
        @current-change="loadTrades"
      /><el-select v-model="pageSize" aria-label="每页条数" @change="applyFilters"
        ><el-option
          v-for="size in [20, 50, 100]"
          :key="size"
          :value="size"
          :label="`${size} 条/页`" /></el-select
      ><span class="total">共 {{ total }} 条</span>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Filter, Refresh } from '@element-plus/icons-vue'
import {
  getPortfolioErrorDetail,
  realPortfolioApi,
  type EventCompleteness,
  type RealTradeItem
} from '@/api/realPortfolio'
import {
  completenessLabel,
  formatDate,
  formatDecimal,
  formatMoney,
  marketLabel,
  warningType
} from './portfolioFormatters'

const dateRange = ref<[string, string] | null>(null)
const market = ref<'' | 'CN' | 'HK'>('')
const eventType = ref('')
const completeness = ref<'' | EventCompleteness>('')
const completenessOptions: EventCompleteness[] = [
  'complete',
  'partial',
  'informational',
  'unclassified'
]
const eventTypes = [
  { value: 'trade', label: '证券成交' },
  { value: 'cash_dividend', label: '现金红利' },
  { value: 'stock_dividend', label: '红股入账' },
  { value: 'cash_interest', label: '利息' },
  { value: 'dividend_tax', label: '红利税' },
  { value: 'portfolio_fee', label: '证券组合费' },
  { value: 'bank_transfer', label: '银证转账' },
  { value: 'security_adjustment_in', label: '股份调入' },
  { value: 'bond_award_notice', label: '新债中签' },
  { value: 'bond_subscription_payment', label: '新债缴款' },
  { value: 'bond_pending_listing', label: '新债待上市' },
  { value: 'bond_listing', label: '新债上市' },
  { value: 'unclassified', label: '未分类' }
]
const loading = ref(false)
const error = ref('')
const trades = ref<RealTradeItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
let requestId = 0

function signedQuantity(value: string | null) {
  const formatted = formatDecimal(value)
  return formatted !== '-' && formatted !== '0' && !formatted.startsWith('-')
    ? `+${formatted}`
    : formatted
}

function cnyMovements(row: RealTradeItem) {
  return row.cash_movements.filter(movement => movement.currency === 'CNY')
}

function applyFilters() {
  page.value = 1
  loadTrades()
}

function clearFilters() {
  dateRange.value = null
  market.value = ''
  eventType.value = ''
  completeness.value = ''
  applyFilters()
}

async function loadTrades() {
  const id = ++requestId
  loading.value = true
  error.value = ''
  try {
    const response = await realPortfolioApi.getTrades({
      date_from: dateRange.value?.[0],
      date_through: dateRange.value?.[1],
      market: market.value || undefined,
      event_type: eventType.value || undefined,
      completeness: completeness.value || undefined,
      page: page.value,
      page_size: pageSize.value
    })
    if (id === requestId) {
      trades.value = response.data.items
      total.value = response.data.total
    }
  } catch (cause: unknown) {
    if (id === requestId) {
      trades.value = []
      total.value = 0
      error.value = getPortfolioErrorDetail(cause)?.message || '成交记录加载失败'
    }
  } finally {
    if (id === requestId) loading.value = false
  }
}

onMounted(loadTrades)
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
.toolbar .el-select {
  width: 145px;
}
.toolbar :deep(.date-range) {
  width: 270px;
  flex: 0 1 270px;
}
.page-header .el-button,
.toolbar .el-button {
  margin: 0;
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
}
.pagination .el-select {
  width: 115px;
}
.pagination {
  justify-content: flex-end;
}
.total {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.warning-line {
  overflow-wrap: anywhere;
}
:deep(.el-table .cell) {
  word-break: normal;
}
:deep(.el-alert__content) {
  min-width: 0;
}
@media (max-width: 767px) {
  .toolbar :deep(.date-range) {
    flex-basis: 100%;
    width: 100%;
    min-width: 0;
  }
  .toolbar .el-select {
    flex: 1 1 140px;
  }
  .pagination {
    justify-content: flex-start;
  }
}
</style>
