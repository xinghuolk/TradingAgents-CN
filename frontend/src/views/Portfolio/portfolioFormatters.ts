import type { DecimalText, EventCompleteness, PortfolioCompleteness } from '@/api/realPortfolio'

type Completeness = PortfolioCompleteness | EventCompleteness

const completenessLabels: Record<Completeness, string> = {
  authoritative: '权威',
  reported_coverage: '区间覆盖',
  incomplete: '不完整',
  complete: '完整',
  partial: '部分',
  informational: '信息',
  unclassified: '未分类'
}

const warningLabels: Record<string, string> = {
  ambiguous_pair: '匹配不明确',
  coverage_gap: '覆盖缺口',
  invalid_delivery_row: '成交记录无效',
  invalid_snapshot_row: '持仓记录无效',
  missing_execution: '缺少成交',
  missing_settlement: '缺少交收',
  partial_snapshot: '部分持仓',
  reported_coverage: '区间覆盖',
  snapshot_replaced: '持仓快照已替换',
  source_fact_replaced: '原始记录已替换',
  unclassified: '未分类记录'
}

const currencySymbols: Record<string, string> = {
  CNY: '¥',
  HKD: 'HK$',
  USD: '$'
}

function normalizeDecimal(value: DecimalText | null | undefined): string | null {
  if (value == null) return null

  const match = value.trim().match(/^([+-]?)(\d+)(?:\.(\d+))?$/)
  if (!match) return null

  const [, rawSign, rawInteger, rawFraction] = match
  const integer = rawInteger.replace(/^0+(?=\d)/, '')
  const fraction = rawFraction?.replace(/0+$/, '')
  const isZero = integer === '0' && !fraction
  const sign = rawSign === '-' && !isZero ? '-' : ''
  const groupedInteger = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')

  return `${sign}${groupedInteger}${fraction ? `.${fraction}` : ''}`
}

export function formatDecimal(value: DecimalText | null | undefined): string {
  return normalizeDecimal(value) ?? '-'
}

export function formatMoney(
  value: DecimalText | null | undefined,
  currency?: string | null
): string {
  const formatted = normalizeDecimal(value)
  if (formatted === null) return '-'
  if (!currency) return formatted

  const normalizedCurrency = currency.trim().toUpperCase()
  if (!normalizedCurrency) return formatted

  const symbol = currencySymbols[normalizedCurrency]
  return symbol ? `${symbol}${formatted}` : `${normalizedCurrency} ${formatted}`
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '-'

  const match = value
    .trim()
    .match(
      /^(\d{4})-(\d{2})-(\d{2})(?:T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?)?$/
    )
  if (!match) return '-'

  const [, year, month, day] = match
  const date = new Date(`${year}-${month}-${day}T00:00:00Z`)
  if (
    Number.isNaN(date.getTime()) ||
    date.getUTCFullYear() !== Number(year) ||
    date.getUTCMonth() + 1 !== Number(month) ||
    date.getUTCDate() !== Number(day)
  ) {
    return '-'
  }

  return `${year}-${month}-${day}`
}

export function marketLabel(value: string | null | undefined): string {
  if (value === 'CN') return 'A股'
  if (value === 'HK') return '港股'
  return '-'
}

export function completenessLabel(value: string | null | undefined): string {
  return completenessLabels[value as Completeness] ?? '-'
}

export function warningType(value: string | null | undefined): string {
  return value ? (warningLabels[value] ?? '-') : '-'
}
