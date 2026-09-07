import axios from 'axios'
import { ApiClient } from './request'

export type DecimalText = string
export type PortfolioCompleteness = 'authoritative' | 'reported_coverage' | 'incomplete'
export type EventCompleteness = 'complete' | 'partial' | 'informational' | 'unclassified'

export interface PortfolioWarning {
  type: string
  message: string
  affects_quantity: boolean
  impact_from: string | null
  impact_through: string | null
}

export interface RealPositionItem {
  security_id: string
  market: 'CN' | 'HK'
  code: string
  name: string
  quantity: DecimalText
  available_quantity: DecimalText | null
  reference_cost: DecimalText | null
  reference_cost_currency: string | null
  broker_market_price: DecimalText | null
  broker_market_price_currency: string | null
  snapshot_market_value: DecimalText | null
  snapshot_market_value_currency: string | null
  snapshot_unrealized_pnl: DecimalText | null
  snapshot_pnl_currency: string | null
  latest_quote_price: DecimalText | null
  latest_quote_currency: string | null
  quote_as_of: string | null
}

export interface CashMovement {
  currency: string
  amount: DecimalText
}

export interface RealTradeItem {
  id: string
  trade_date: string | null
  settlement_date: string | null
  security_id: string | null
  market: 'CN' | 'HK' | null
  code: string | null
  name: string | null
  event_type: string
  operation_label: string
  security_quantity: DecimalText | null
  trade_currency: string | null
  cash_movements: CashMovement[]
  completeness: EventCompleteness
  warnings: PortfolioWarning[]
}

export interface ImportSummary {
  status: 'preview' | 'imported' | 'duplicate'
  source_type: 'snapshot' | 'delivery_statement'
  format_id: string
  file_sha256_short: string
  source_rows: number
  usable_rows: number
  new_facts: number
  duplicate_facts: number
  conflicting_facts: number
  events: number
  partial_events: number
  unclassified_events: number
  warnings: PortfolioWarning[]
}

export interface ImportHistoryItem {
  completed_at: string | null
  source_type: 'snapshot' | 'delivery_statement'
  format_id: string
  source_filename: string
  file_sha256_short: string
  row_count: number
  observed_on: string | null
  coverage_from: string | null
  coverage_through: string | null
  status: 'publishing' | 'imported' | 'failed'
  warning_count: number
}

export interface Page<T> {
  items: T[]
  page: number
  page_size: number
  total: number
}

export interface PortfolioErrorDetail {
  code: string
  message: string
  source_type?: 'snapshot'
  observed_on?: string
}

export interface TradeQuery {
  date_from?: string
  date_through?: string
  market?: 'CN' | 'HK'
  security_id?: string
  event_type?: string
  completeness?: EventCompleteness
  page?: number
  page_size?: number
}

export interface PositionResponse {
  account_alias: 'main'
  requested_date: string
  anchor_date: string
  direction: 'exact' | 'partial_snapshot' | 'forward' | 'reverse'
  completeness: PortfolioCompleteness
  reported_coverage: Array<{ from: string; through: string }>
  warning_count: number
  warnings: PortfolioWarning[]
  items: RealPositionItem[]
}

export const realPortfolioApi = {
  async importFile(file: File, options: { asOf?: string; dryRun?: boolean }) {
    const body = new FormData()
    body.append('file', file)
    if (options.asOf) body.append('as_of', options.asOf)
    body.append('dry_run', String(options.dryRun ?? false))

    return ApiClient.post<ImportSummary>('/api/real-portfolio/import', body, {
      headers: { 'Content-Type': 'multipart/form-data' },
      skipErrorHandler: true,
      showLoading: true
    })
  },

  async getPositions(asOf?: string) {
    return ApiClient.get<PositionResponse>('/api/real-portfolio/positions', {
      as_of: asOf
    })
  },

  async getTrades(params: TradeQuery) {
    return ApiClient.get<Page<RealTradeItem>>('/api/real-portfolio/trades', params)
  },

  async getImports(page = 1, pageSize = 50) {
    return ApiClient.get<Page<ImportHistoryItem>>('/api/real-portfolio/imports', {
      page,
      page_size: pageSize
    })
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function getPortfolioErrorDetail(error: unknown): PortfolioErrorDetail | null {
  if (!axios.isAxiosError(error) || !isRecord(error.response?.data)) return null

  const detail = error.response.data.detail
  if (!isRecord(detail) || typeof detail.code !== 'string' || typeof detail.message !== 'string') {
    return null
  }

  const result: PortfolioErrorDetail = {
    code: detail.code,
    message: detail.message
  }
  if (detail.source_type === 'snapshot') result.source_type = detail.source_type
  if (typeof detail.observed_on === 'string') result.observed_on = detail.observed_on
  return result
}
