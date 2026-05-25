<template>
  <div class="turtle-payload-panel">
    <!-- No payload: markdown-only fallback -->
    <div v-if="!parsedPayload" class="markdown-only">
      <div
        v-if="valueReport"
        class="markdown-content"
        v-html="renderMarkdown(valueReport)"
      />
      <el-empty v-else description="暂无报告内容" />
    </div>

    <!-- Payload available: sub-tabs -->
    <el-tabs v-else v-model="activeTab" type="card" class="turtle-sub-tabs">
      <!-- 报告 tab -->
      <el-tab-pane label="报告" name="report">
        <div class="tab-content">
          <div
            v-if="valueReport"
            class="markdown-content"
            v-html="renderMarkdown(valueReport)"
          />
          <el-empty v-else description="暂无报告正文" />
        </div>
      </el-tab-pane>

      <!-- 数据 tab -->
      <el-tab-pane label="数据" name="data">
        <div class="tab-content">
          <!-- 汇率与来源 (M1 provenance block) -->
          <template v-if="fxRateRows.length > 0 || marketProv.marketAsOf || marketProv.provider">
            <div class="provenance-section">
              <div class="section-title">汇率与来源</div>

              <!-- Market metadata -->
              <div v-if="marketProv.marketAsOf || marketProv.provider" class="market-prov">
                <el-descriptions :column="2" size="small" border>
                  <el-descriptions-item v-if="marketProv.provider" label="市场数据来源">
                    {{ marketProv.provider }}
                  </el-descriptions-item>
                  <el-descriptions-item v-if="marketProv.marketAsOf" label="市值快照日">
                    {{ marketProv.marketAsOf }}
                  </el-descriptions-item>
                </el-descriptions>
              </div>

              <!-- FX rates table -->
              <el-table
                v-if="fxRateRows.length > 0"
                :data="fxRateRows"
                size="small"
                class="fx-table"
                border
              >
                <el-table-column prop="pair" label="货币对" width="120" />
                <el-table-column label="汇率" width="100">
                  <template #default="{ row }">
                    {{ row.rate.toFixed(6) }}
                  </template>
                </el-table-column>
                <el-table-column label="来源" width="180">
                  <template #default="{ row }">
                    <el-tag v-if="row.isDerived" type="info" size="small">派生 (via CNY)</el-tag>
                    <el-tag v-else type="success" size="small">直连</el-tag>
                    <span class="provider-text">{{ row.provider }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="as_of" width="130">
                  <template #default="{ row }">{{ row.asOf ?? '—' }}</template>
                </el-table-column>
                <el-table-column label="derived_from">
                  <template #default="{ row }">
                    <span v-if="row.derivedFrom && row.derivedFrom.length">
                      {{ row.derivedFrom.join(' + ') }}
                    </span>
                    <span v-else>—</span>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </template>

          <!-- Report facts -->
          <template v-if="reportFields.length > 0">
            <div class="section-title">财务报告数据</div>
            <el-table :data="reportFields" size="small" border class="facts-table">
              <el-table-column prop="name" label="字段" width="180" />
              <el-table-column label="值" width="180">
                <template #default="{ row }">{{ row.formattedValue }}</template>
              </el-table-column>
              <el-table-column label="可靠性" width="100">
                <template #default="{ row }">
                  <el-tag v-if="row.reliability" :type="reliabilityTagType(row.reliability)" size="small">
                    {{ row.reliability }}
                  </el-tag>
                  <span v-else>—</span>
                </template>
              </el-table-column>
              <el-table-column label="来源标签" width="160">
                <template #default="{ row }">{{ row.source_label ?? '—' }}</template>
              </el-table-column>
              <el-table-column label="来源引用">
                <template #default="{ row }">
                  <div class="source-ref-cell" :title="row.source_reference">
                    <!-- Field path (always shown when non-empty) -->
                    <span v-if="row.parsedRef.rest" class="ref-segment">{{ row.parsedRef.rest }}</span>
                    <!-- Page chips (M2) -->
                    <el-tag
                      v-for="page in row.parsedRef.pages"
                      :key="page"
                      size="small"
                      type="warning"
                      class="page-chip"
                      style="cursor: pointer; margin-right: 4px;"
                      @click="handlePageChipClick()"
                    >
                      p.{{ page }}
                    </el-tag>
                    <!-- Provider -->
                    <span v-if="row.parsedRef.provider" class="ref-segment">{{ row.parsedRef.provider }}</span>
                    <!-- FX -->
                    <span v-if="row.parsedRef.fx" class="ref-segment ref-fx">FX {{ row.parsedRef.fx }}</span>
                    <!-- fetched_at -->
                    <span v-if="row.parsedRef.fetchedAt" class="ref-segment ref-date">{{ row.parsedRef.fetchedAt }}</span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="备注" min-width="120">
                <template #default="{ row }">{{ row.caveat ?? '—' }}</template>
              </el-table-column>
            </el-table>
          </template>

          <!-- Market facts -->
          <template v-if="marketFields.length > 0">
            <div class="section-title" style="margin-top: 16px;">市场数据</div>
            <el-table :data="marketFields" size="small" border class="facts-table">
              <el-table-column prop="name" label="字段" width="180" />
              <el-table-column label="值" width="180">
                <template #default="{ row }">{{ row.formattedValue }}</template>
              </el-table-column>
              <el-table-column label="可靠性" width="100">
                <template #default="{ row }">
                  <el-tag v-if="row.reliability" :type="reliabilityTagType(row.reliability)" size="small">
                    {{ row.reliability }}
                  </el-tag>
                  <span v-else>—</span>
                </template>
              </el-table-column>
              <el-table-column label="来源标签" width="160">
                <template #default="{ row }">{{ row.source_label ?? '—' }}</template>
              </el-table-column>
              <el-table-column label="来源引用">
                <template #default="{ row }">
                  <div class="source-ref-cell" :title="row.source_reference">
                    <!-- Field path (always shown when non-empty) -->
                    <span v-if="row.parsedRef.rest" class="ref-segment">{{ row.parsedRef.rest }}</span>
                    <el-tag
                      v-for="page in row.parsedRef.pages"
                      :key="page"
                      size="small"
                      type="warning"
                      class="page-chip"
                      style="cursor: pointer; margin-right: 4px;"
                      @click="handlePageChipClick()"
                    >
                      p.{{ page }}
                    </el-tag>
                    <span v-if="row.parsedRef.provider" class="ref-segment">{{ row.parsedRef.provider }}</span>
                    <span v-if="row.parsedRef.fx" class="ref-segment ref-fx">FX {{ row.parsedRef.fx }}</span>
                    <span v-if="row.parsedRef.fetchedAt" class="ref-segment ref-date">{{ row.parsedRef.fetchedAt }}</span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="备注" min-width="120">
                <template #default="{ row }">{{ row.caveat ?? '—' }}</template>
              </el-table-column>
            </el-table>
          </template>

          <!-- Historical facts (collapsible) -->
          <template v-if="historicalPeriods.length > 0">
            <div class="section-title" style="margin-top: 16px;">历史期间</div>
            <el-collapse>
              <el-collapse-item
                v-for="period in historicalPeriods"
                :key="period.periodKey"
                :title="period.periodKey"
              >
                <el-table :data="period.fields" size="small" border class="facts-table">
                  <el-table-column prop="name" label="字段" width="180" />
                  <el-table-column label="值" width="180">
                    <template #default="{ row }">{{ row.formattedValue }}</template>
                  </el-table-column>
                  <el-table-column label="可靠性" width="100">
                    <template #default="{ row }">
                      <el-tag v-if="row.reliability" :type="reliabilityTagType(row.reliability)" size="small">
                        {{ row.reliability }}
                      </el-tag>
                      <span v-else>—</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="来源引用" min-width="160">
                    <template #default="{ row }">{{ row.source_reference ?? '—' }}</template>
                  </el-table-column>
                </el-table>
              </el-collapse-item>
            </el-collapse>
          </template>

          <el-empty
            v-if="reportFields.length === 0 && marketFields.length === 0 && historicalPeriods.length === 0 && fxRateRows.length === 0"
            description="暂无数据字段"
          />
        </div>
      </el-tab-pane>

      <!-- 计算 tab -->
      <el-tab-pane label="计算" name="signals">
        <div class="tab-content">
          <template v-if="signalRows.length > 0">
            <el-table :data="signalRows" size="small" border class="signals-table">
              <el-table-column prop="name" label="指标" width="160" />
              <el-table-column label="状态" width="130">
                <template #default="{ row }">
                  <el-tag :type="statusTagType(row.status ?? '')" size="small">
                    {{ row.status ?? '—' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="formula" label="公式" min-width="200" />
              <el-table-column prop="substitution" label="代入" min-width="200" />
              <el-table-column label="结果" width="120">
                <template #default="{ row }">
                  {{ formatFactValue(row.value) }}
                  <span v-if="row.unit"> {{ row.unit }}</span>
                </template>
              </el-table-column>
              <el-table-column label="缺失输入" min-width="160">
                <template #default="{ row }">
                  <template v-if="row.missing_inputs && row.missing_inputs.length > 0">
                    <el-tag
                      v-for="mi in row.missing_inputs"
                      :key="mi"
                      type="danger"
                      size="small"
                      style="margin-right: 4px; margin-bottom: 2px;"
                    >{{ mi }}</el-tag>
                  </template>
                  <span v-else>—</span>
                </template>
              </el-table-column>
              <el-table-column label="来源" min-width="160">
                <template #default="{ row }">
                  <template v-if="row.sources && row.sources.length > 0">
                    <el-tag
                      v-for="src in row.sources"
                      :key="src"
                      type="info"
                      size="small"
                      style="margin-right: 4px; margin-bottom: 2px;"
                    >{{ src }}</el-tag>
                  </template>
                  <span v-else>—</span>
                </template>
              </el-table-column>
            </el-table>
          </template>
          <el-empty v-else description="暂无计算结果" />

          <!-- Veto reasons -->
          <template v-if="vetoReasons.length > 0">
            <div class="section-title" style="margin-top: 16px;">否决原因</div>
            <el-alert
              v-for="(reason, i) in vetoReasons"
              :key="i"
              :title="reason"
              type="warning"
              show-icon
              :closable="false"
              style="margin-bottom: 8px;"
            />
          </template>
        </div>
      </el-tab-pane>

      <!-- 状态 tab -->
      <el-tab-pane label="状态" name="status">
        <div class="tab-content">
          <!-- Status summary -->
          <el-descriptions title="状态概览" :column="2" border size="small" class="status-descriptions">
            <el-descriptions-item label="facts 状态">
              <el-tag :type="statusTagType(parsedPayload.facts?.status ?? '')" size="small">
                {{ parsedPayload.facts?.status ?? '—' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="signals 状态">
              <el-tag :type="statusTagType(parsedPayload.signals?.status ?? '')" size="small">
                {{ parsedPayload.signals?.status ?? '—' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="report facts 状态">
              <el-tag :type="statusTagType(parsedPayload.facts?.report?.status ?? '')" size="small">
                {{ parsedPayload.facts?.report?.status ?? '—' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="market facts 状态">
              <el-tag :type="statusTagType(parsedPayload.facts?.market?.status ?? '')" size="small">
                {{ parsedPayload.facts?.market?.status ?? '—' }}
              </el-tag>
            </el-descriptions-item>
          </el-descriptions>

          <!-- Caveats -->
          <template v-if="allCaveats.length > 0">
            <div class="section-title" style="margin-top: 16px;">注意事项</div>
            <el-alert
              v-for="(cav, i) in allCaveats"
              :key="i"
              :title="cav"
              type="warning"
              show-icon
              :closable="false"
              style="margin-bottom: 6px;"
            />
          </template>

          <!-- Missing inputs aggregated from signals -->
          <template v-if="allMissingInputs.length > 0">
            <div class="section-title" style="margin-top: 16px;">缺失输入汇总</div>
            <el-tag
              v-for="mi in allMissingInputs"
              :key="mi"
              type="danger"
              size="small"
              style="margin-right: 6px; margin-bottom: 6px;"
            >{{ mi }}</el-tag>
          </template>

          <el-empty
            v-if="allCaveats.length === 0 && allMissingInputs.length === 0"
            description="无注意事项"
          />
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { marked } from 'marked'
import {
  parseTurtlePayload,
  formatFactValue,
  parseSourceReference,
  extractFxRates,
  extractMarketProvenance,
  statusTagType,
  reliabilityTagType,
  type ParsedTurtlePayload,
  type FactField,
  type FxRateRow,
  type MarketProvenance,
} from '@/utils/turtlePayload'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface Props {
  valueReport?: string
  valueTurtlePayload?: string
}

const props = withDefaults(defineProps<Props>(), {
  valueReport: '',
  valueTurtlePayload: '',
})

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const activeTab = ref<string>('report')

// ---------------------------------------------------------------------------
// Parsed payload (reactive)
// ---------------------------------------------------------------------------

const parsedPayload = computed<ParsedTurtlePayload | null>(() =>
  parseTurtlePayload(props.valueTurtlePayload)
)

// ---------------------------------------------------------------------------
// Markdown renderer
// ---------------------------------------------------------------------------

marked.setOptions({ breaks: true, gfm: true })

function renderMarkdown(content: string): string {
  if (!content) return ''
  try {
    return marked.parse(content) as string
  } catch {
    return `<pre style="white-space: pre-wrap;">${content}</pre>`
  }
}

// ---------------------------------------------------------------------------
// M1 Provenance computed
// ---------------------------------------------------------------------------

const fxRateRows = computed<FxRateRow[]>(() => {
  const reportMeta = parsedPayload.value?.facts?.report?.metadata
  return extractFxRates(reportMeta)
})

const marketProv = computed<MarketProvenance>(() => {
  const marketMeta = parsedPayload.value?.facts?.market?.metadata
  return extractMarketProvenance(marketMeta)
})

// ---------------------------------------------------------------------------
// Facts field rows (数据 tab)
// ---------------------------------------------------------------------------

interface FactRow {
  name: string
  formattedValue: string
  reliability?: string
  source_label?: string
  source_reference?: string
  parsedRef: ReturnType<typeof parseSourceReference>
  caveat?: string
}

function fieldsToRows(fields: Record<string, FactField> | undefined): FactRow[] {
  if (!fields) return []
  return Object.entries(fields).map(([key, field]) => ({
    name: field.name ?? key,
    formattedValue: formatFactValue(field.value),
    reliability: field.reliability,
    source_label: field.source_label,
    source_reference: field.source_reference,
    parsedRef: parseSourceReference(field.source_reference),
    caveat: field.caveat,
  }))
}

const reportFields = computed<FactRow[]>(() =>
  fieldsToRows(parsedPayload.value?.facts?.report?.fields)
)

const marketFields = computed<FactRow[]>(() =>
  fieldsToRows(parsedPayload.value?.facts?.market?.fields)
)

// ---------------------------------------------------------------------------
// Historical periods
// ---------------------------------------------------------------------------

interface HistoricalPeriod {
  periodKey: string
  fields: FactRow[]
}

const historicalPeriods = computed<HistoricalPeriod[]>(() => {
  const historical = parsedPayload.value?.facts?.report?.historical
  if (!historical || typeof historical !== 'object') return []
  return Object.entries(historical).map(([periodKey, periodData]) => ({
    periodKey,
    fields: fieldsToRows(periodData?.fields),
  }))
})

// ---------------------------------------------------------------------------
// Signals rows (计算 tab)
// ---------------------------------------------------------------------------

interface SignalRow {
  name: string
  status?: string
  formula?: string
  substitution?: string
  value: unknown
  unit?: string
  sources?: string[]
  missing_inputs?: string[]
}

const signalRows = computed<SignalRow[]>(() => {
  const results = parsedPayload.value?.signals?.results
  if (!results) return []
  return Object.entries(results).map(([key, sr]) => ({
    name: sr.name ?? key,
    status: sr.status,
    formula: sr.formula,
    substitution: sr.substitution,
    value: sr.value,
    unit: sr.unit,
    sources: sr.sources,
    missing_inputs: sr.missing_inputs,
  }))
})

const vetoReasons = computed<string[]>(() =>
  parsedPayload.value?.signals?.veto_reasons ?? []
)

// ---------------------------------------------------------------------------
// Status tab computed
// ---------------------------------------------------------------------------

const allCaveats = computed<string[]>(() => {
  const pl = parsedPayload.value
  if (!pl) return []
  return [
    ...(pl.facts?.caveats ?? []),
    ...(pl.facts?.report?.caveats ?? []),
    ...(pl.facts?.market?.caveats ?? []),
    ...(pl.signals?.caveats ?? []),
  ]
})

const allMissingInputs = computed<string[]>(() => {
  const results = parsedPayload.value?.signals?.results
  if (!results) return []
  const seen = new Set<string>()
  const out: string[] = []
  for (const sr of Object.values(results)) {
    for (const mi of (sr.missing_inputs ?? [])) {
      if (!seen.has(mi)) {
        seen.add(mi)
        out.push(mi)
      }
    }
  }
  return out
})

// ---------------------------------------------------------------------------
// Page chip click handler (spec §7)
// ---------------------------------------------------------------------------

function handlePageChipClick(): void {
  ElMessage.info('当前报告暂未提供原文定位链接')
}
</script>

<style scoped>
.turtle-payload-panel {
  width: 100%;
}

/* Isolated sub-tab styles — must NOT inherit outer .analysis-tabs :deep rules */
.turtle-sub-tabs {
  margin-top: 8px;
}

/* Override to keep sub-tabs compact (counteract outer :deep(.el-tabs__item) 55px height) */
.turtle-sub-tabs :deep(.el-tabs__item) {
  height: 36px !important;
  line-height: 36px !important;
  padding: 0 14px !important;
  margin-right: 4px !important;
  background: var(--el-bg-color) !important;
  border: 1px solid var(--el-border-color) !important;
  border-radius: 6px !important;
  color: var(--el-text-color-regular) !important;
  font-weight: 500 !important;
  transform: none !important;
  box-shadow: none !important;
  font-size: 13px !important;
}

.turtle-sub-tabs :deep(.el-tabs__item.is-active) {
  background: var(--el-color-primary-light-9) !important;
  color: var(--el-color-primary) !important;
  border-color: var(--el-color-primary-light-5) !important;
  transform: none !important;
  box-shadow: none !important;
}

.turtle-sub-tabs :deep(.el-tabs__item:hover) {
  background: var(--el-fill-color-light) !important;
  transform: none !important;
  box-shadow: none !important;
}

.turtle-sub-tabs :deep(.el-tabs__header) {
  margin: 0 0 12px 0;
  background: transparent;
  padding: 0;
  border-radius: 0;
  box-shadow: none;
  border: none;
}

.tab-content {
  padding: 8px 0;
}

.section-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--el-text-color-primary);
  margin-bottom: 8px;
  padding-left: 4px;
  border-left: 3px solid var(--el-color-primary);
}

.provenance-section {
  margin-bottom: 16px;
  padding: 12px;
  background: var(--el-fill-color-extra-light);
  border-radius: 6px;
  border: 1px solid var(--el-border-color-light);
}

.market-prov {
  margin-bottom: 10px;
}

.fx-table {
  margin-top: 8px;
}

.provider-text {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-left: 4px;
}

.facts-table,
.signals-table {
  width: 100%;
}

.source-ref-cell {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.page-chip {
  cursor: pointer;
}

.ref-segment {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.ref-fx {
  color: var(--el-color-warning);
}

.ref-date {
  color: var(--el-text-color-placeholder);
}

.status-descriptions {
  margin-bottom: 8px;
}

.markdown-content {
  line-height: 1.7;
}

.markdown-only {
  padding: 4px 0;
}
</style>
