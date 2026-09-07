<template>
  <section class="portfolio-page" aria-labelledby="holdings-title">
    <header class="page-header">
      <h1 id="holdings-title">真实持仓</h1>
      <div class="toolbar">
        <el-date-picker
          v-model="selectedDate"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="持仓日期"
          aria-label="持仓日期"
          @change="loadPositions"
        />
        <el-tooltip content="刷新"
          ><el-button :icon="Refresh" :loading="loading" aria-label="刷新" @click="loadPositions"
        /></el-tooltip>
      </div>
    </header>
    <el-descriptions v-if="portfolio" :column="isMobile ? 1 : 5" size="small" border>
      <el-descriptions-item label="账户">{{ portfolio.account_alias }}</el-descriptions-item>
      <el-descriptions-item label="持仓日期">{{
        formatDate(portfolio.requested_date)
      }}</el-descriptions-item>
      <el-descriptions-item label="快照锚点">{{
        formatDate(portfolio.anchor_date)
      }}</el-descriptions-item>
      <el-descriptions-item label="完整性"
        ><el-tag
          :type="portfolio.completeness === 'authoritative' ? 'success' : 'warning'"
          size="small"
          >{{ completenessLabel(portfolio.completeness) }}</el-tag
        ></el-descriptions-item
      >
      <el-descriptions-item label="警告">{{ portfolio.warning_count }}</el-descriptions-item>
    </el-descriptions>
    <el-alert
      v-if="portfolio && (portfolio.warning_count || portfolio.completeness !== 'authoritative')"
      type="warning"
      :closable="false"
      show-icon
      :title="completenessLabel(portfolio.completeness)"
    >
      <div v-for="(warning, index) in portfolio.warnings" :key="index" class="warning-line">
        {{ warningType(warning.type) }}：{{ warning.message }}
      </div>
    </el-alert>
    <div v-if="securityFilter" class="filter-row">
      <el-tag>{{ securityFilter }}</el-tag>
      <el-tooltip content="清除证券筛选"
        ><el-button :icon="Close" aria-label="清除证券筛选" @click="clearSecurityFilter"
      /></el-tooltip>
    </div>
    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error"
      ><el-button :icon="Refresh" :loading="loading" @click="loadPositions"
        >重试</el-button
      ></el-alert
    >
    <el-table
      v-loading="loading"
      :data="rows"
      row-key="security_id"
      height="460"
      stripe
      empty-text="暂无数据"
      :row-class-name="() => (securityFilter ? 'selected-security' : '')"
    >
      <el-table-column label="市场" width="80"
        ><template #default="{ row }">{{ marketLabel(row.market) }}</template></el-table-column
      >
      <el-table-column label="证券代码" width="110"
        ><template #default="{ row }"
          ><router-link :to="{ name: 'StockDetail', params: { code: row.code } }">{{
            row.code
          }}</router-link></template
        ></el-table-column
      >
      <el-table-column prop="name" label="证券名称" min-width="150" show-overflow-tooltip />
      <el-table-column label="持仓数量" width="140" align="right"
        ><template #default="{ row }">{{ formatDecimal(row.quantity) }}</template></el-table-column
      >
      <el-table-column label="可用数量" width="140" align="right"
        ><template #default="{ row }">{{
          formatDecimal(row.available_quantity)
        }}</template></el-table-column
      >
      <el-table-column label="参考成本" width="150" align="right"
        ><template #default="{ row }">{{
          formatMoney(row.reference_cost, row.reference_cost_currency)
        }}</template></el-table-column
      >
      <el-table-column label="券商快照价格" width="160" align="right"
        ><template #default="{ row }">{{
          formatMoney(row.broker_market_price, row.broker_market_price_currency)
        }}</template></el-table-column
      >
      <el-table-column label="券商快照市值" width="170" align="right"
        ><template #default="{ row }">{{
          formatMoney(row.snapshot_market_value, row.snapshot_market_value_currency)
        }}</template></el-table-column
      >
      <el-table-column label="券商快照浮动盈亏" width="180" align="right"
        ><template #default="{ row }">{{
          formatMoney(row.snapshot_unrealized_pnl, row.snapshot_pnl_currency)
        }}</template></el-table-column
      >
      <el-table-column label="最新行情" width="155" align="right"
        ><template #default="{ row }">{{
          formatMoney(row.latest_quote_price, row.latest_quote_currency)
        }}</template></el-table-column
      >
      <el-table-column label="行情时间" width="200"
        ><template #default="{ row }">{{ row.quote_as_of || '-' }}</template></el-table-column
      >
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMediaQuery } from '@vueuse/core'
import { Close, Refresh } from '@element-plus/icons-vue'
import {
  getPortfolioErrorDetail,
  realPortfolioApi,
  type PositionResponse
} from '@/api/realPortfolio'
import {
  completenessLabel,
  formatDate,
  formatDecimal,
  formatMoney,
  marketLabel,
  warningType
} from './portfolioFormatters'

const route = useRoute()
const router = useRouter()
const isMobile = useMediaQuery('(max-width: 767px)')
const initialDate = typeof route.query.as_of === 'string' ? formatDate(route.query.as_of) : '-'
const selectedDate = ref<string | undefined>(initialDate === '-' ? undefined : initialDate)
const loading = ref(false)
const error = ref('')
const portfolio = ref<PositionResponse | null>(null)
const securityFilter = computed(() =>
  typeof route.query.security_id === 'string' ? route.query.security_id : ''
)
const rows = computed(() =>
  (portfolio.value?.items || []).filter(
    row => !securityFilter.value || row.security_id === securityFilter.value
  )
)
let requestId = 0

function clearSecurityFilter() {
  const query = { ...route.query }
  delete query.security_id
  if (selectedDate.value) query.as_of = selectedDate.value
  else delete query.as_of
  router.replace({ query })
}

async function loadPositions() {
  const id = ++requestId
  loading.value = true
  error.value = ''
  try {
    const response = await realPortfolioApi.getPositions(selectedDate.value || undefined)
    if (id === requestId) portfolio.value = response.data
  } catch (cause: unknown) {
    if (id === requestId) {
      portfolio.value = null
      error.value = getPortfolioErrorDetail(cause)?.message || '持仓加载失败'
    }
  } finally {
    if (id === requestId) loading.value = false
  }
}

onMounted(loadPositions)
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
.filter-row {
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
.toolbar :deep(.el-date-editor) {
  width: 180px;
}
.toolbar .el-button,
.filter-row .el-button {
  margin: 0;
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
}
.warning-line {
  overflow-wrap: anywhere;
}
:deep(.el-alert__content) {
  min-width: 0;
}
:deep(.el-table .cell) {
  word-break: normal;
}
:deep(.selected-security) {
  --el-table-tr-bg-color: var(--el-color-primary-light-9);
}
a {
  color: var(--el-color-primary);
  text-decoration: none;
}
@media (max-width: 767px) {
  .toolbar {
    width: 100%;
  }
}
</style>
