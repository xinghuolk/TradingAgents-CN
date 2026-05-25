/**
 * Pure helper functions for TurtleFacts/TurtleSignals payload parsing and formatting.
 * All functions are side-effect-free and suitable for unit testing.
 * (Spec 4 §5.1, M1 provenance, M2 source_reference parsing)
 */

// ---------------------------------------------------------------------------
// Types / Interfaces
// ---------------------------------------------------------------------------

export interface MoneyAmount {
  value: number
  currency?: string
  unit?: string
}

export interface FactField {
  name?: string
  value: unknown
  reliability?: string
  source_label?: string
  source_reference?: string
  caveat?: string
  unit?: string
}

export interface ReportFacts {
  fields?: Record<string, FactField>
  metadata?: {
    fx_rates?: Record<string, number>
    fx_rates_meta?: Record<string, FxRateMeta>
    period_end?: string
    [key: string]: unknown
  }
  status?: string
  caveats?: string[]
  historical?: Record<string, ReportFacts>
}

export interface MarketFacts {
  fields?: Record<string, FactField>
  metadata?: {
    market_as_of?: string
    provider?: string
    [key: string]: unknown
  }
  status?: string
  caveats?: string[]
}

export interface Facts {
  report?: ReportFacts
  market?: MarketFacts
  status?: string
  caveats?: string[]
}

export interface SignalResult {
  name?: string
  status?: string
  formula?: string
  substitution?: string
  value?: unknown
  unit?: string
  sources?: string[]
  missing_inputs?: string[]
}

export interface Signals {
  status?: string
  results?: Record<string, SignalResult>
  veto_reasons?: string[]
  caveats?: string[]
}

export interface ParsedTurtlePayload {
  facts: Facts
  signals: Signals
}

export interface FxRateMeta {
  provider?: string
  as_of?: string
  fetched_at?: string
  rate?: number
  derived_from?: string[]
}

export interface FxRateRow {
  pair: string
  rate: number
  provider: string
  asOf?: string
  fetchedAt?: string
  derivedFrom?: string[]
  isDerived: boolean
}

export interface MarketProvenance {
  marketAsOf?: string
  provider?: string
}

export interface ParsedSourceReference {
  pages: number[]
  provider?: string
  fetchedAt?: string
  fx?: string
  rest: string
}

// ---------------------------------------------------------------------------
// parseTurtlePayload
// ---------------------------------------------------------------------------

/**
 * Parse the raw value_turtle_payload JSON string.
 * Returns null if the string is blank, missing, or not valid JSON.
 * Logs a console.warn on JSON parse failure (spec §7).
 */
export function parseTurtlePayload(raw: string | null | undefined): ParsedTurtlePayload | null {
  if (!raw || !raw.trim()) {
    return null
  }
  try {
    const parsed = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) {
      console.warn('[TurtlePayload] Parsed payload is not an object:', typeof parsed)
      return null
    }
    return parsed as ParsedTurtlePayload
  } catch (e) {
    console.warn('[TurtlePayload] Failed to parse value_turtle_payload JSON:', e)
    return null
  }
}

// ---------------------------------------------------------------------------
// formatFactValue
// ---------------------------------------------------------------------------

/**
 * Format a fact field value for display.
 * - MoneyAmount objects: "{value} {currency} {unit}"
 * - null/undefined: "—"
 * - numbers: toLocaleString with up to 4 decimal places
 * - everything else: String()
 */
export function formatFactValue(value: unknown): string {
  if (value === null || value === undefined) {
    return '—'
  }
  if (typeof value === 'object' && !Array.isArray(value)) {
    const obj = value as Record<string, unknown>
    if ('value' in obj) {
      const num = obj.value
      const numStr = typeof num === 'number'
        ? num.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
        : String(num)
      const currency = obj.currency ? ` ${obj.currency}` : ''
      const unit = obj.unit ? ` ${obj.unit}` : ''
      return `${numStr}${currency}${unit}`.trim()
    }
    return JSON.stringify(obj)
  }
  if (typeof value === 'number') {
    return value.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
  }
  if (typeof value === 'boolean') {
    return value ? '是' : '否'
  }
  return String(value)
}

// ---------------------------------------------------------------------------
// extractPageRefs
// ---------------------------------------------------------------------------

/**
 * Extract page numbers from a source_reference string.
 * Matches "p.<number>" patterns (e.g. "net_profit p.7" → [7]).
 * Multiple matches: "p.7 p.12" → [7, 12].
 */
export function extractPageRefs(sourceReference: string | null | undefined): number[] {
  if (!sourceReference) return []
  const matches = sourceReference.match(/p\.(\d+)/g)
  if (!matches) return []
  return matches.map(m => parseInt(m.replace('p.', ''), 10)).filter(n => !isNaN(n))
}

// ---------------------------------------------------------------------------
// parseSourceReference (M2)
// ---------------------------------------------------------------------------

/**
 * Parse a composite source_reference string (Spec 3 / M2).
 *
 * Examples:
 *   "market_data.market_cap; provider=yfinance_hk; fetched_at=2026-05-23T10:00:00; FX HKD:CNY=0.92"
 *     → { pages: [], provider: "yfinance_hk", fetchedAt: "2026-05-23T10:00:00", fx: "HKD:CNY=0.92", rest: "market_data.market_cap" }
 *   "net_profit p.7"
 *     → { pages: [7], provider: undefined, fetchedAt: undefined, fx: undefined, rest: "net_profit p.7" }
 *
 * Parsing is lenient — missing segments leave the field undefined.
 */
export function parseSourceReference(raw: string | null | undefined): ParsedSourceReference {
  if (!raw) {
    return { pages: [], rest: '' }
  }

  const pages = extractPageRefs(raw)

  // Split on semicolon to get segments
  const segments = raw.split(';').map(s => s.trim()).filter(Boolean)

  let provider: string | undefined
  let fetchedAt: string | undefined
  let fx: string | undefined
  const restParts: string[] = []

  for (const seg of segments) {
    const providerMatch = seg.match(/^provider=(.+)$/)
    if (providerMatch) {
      provider = providerMatch[1].trim()
      continue
    }
    const fetchedAtMatch = seg.match(/^fetched_at=(.+)$/)
    if (fetchedAtMatch) {
      fetchedAt = fetchedAtMatch[1].trim()
      continue
    }
    const fxMatch = seg.match(/^FX\s+(.+)$/)
    if (fxMatch) {
      fx = fxMatch[1].trim()
      continue
    }
    restParts.push(seg)
  }

  return {
    pages,
    provider,
    fetchedAt,
    fx,
    rest: restParts.join('; '),
  }
}

// ---------------------------------------------------------------------------
// extractFxRates (M1)
// ---------------------------------------------------------------------------

/**
 * Merge fx_rates + fx_rates_meta from report metadata into a display row list.
 * - Direct pairs (provider=yfinance): isDerived=false.
 * - Derived pairs (provider=derived(...) or derived_from present): isDerived=true.
 * - Returns [] when fx_rates is absent or empty (single-currency / no FX triggered).
 * Lenient parsing — missing fields yield undefined, never throws.
 */
export function extractFxRates(reportMetadata: unknown): FxRateRow[] {
  if (!reportMetadata || typeof reportMetadata !== 'object') return []
  const meta = reportMetadata as Record<string, unknown>

  const fxRates = meta.fx_rates
  if (!fxRates || typeof fxRates !== 'object') return []
  const ratesObj = fxRates as Record<string, number>

  const fxRatesMeta = (meta.fx_rates_meta ?? {}) as Record<string, FxRateMeta>

  const rows: FxRateRow[] = []
  for (const [pair, rate] of Object.entries(ratesObj)) {
    if (typeof rate !== 'number') continue
    const pairMeta = (fxRatesMeta[pair] ?? {}) as FxRateMeta
    const provider = pairMeta.provider ?? 'unknown'
    const isDerived = provider.startsWith('derived') || (Array.isArray(pairMeta.derived_from) && pairMeta.derived_from.length > 0)
    rows.push({
      pair,
      rate,
      provider,
      asOf: pairMeta.as_of,
      fetchedAt: pairMeta.fetched_at,
      derivedFrom: pairMeta.derived_from,
      isDerived,
    })
  }
  return rows
}

// ---------------------------------------------------------------------------
// extractMarketProvenance (M1)
// ---------------------------------------------------------------------------

/**
 * Extract market provenance from market.metadata.
 * Returns { marketAsOf, provider } with undefined for missing keys.
 * Never throws.
 */
export function extractMarketProvenance(marketMetadata: unknown): MarketProvenance {
  if (!marketMetadata || typeof marketMetadata !== 'object') {
    return {}
  }
  const meta = marketMetadata as Record<string, unknown>
  return {
    marketAsOf: typeof meta.market_as_of === 'string' ? meta.market_as_of : undefined,
    provider: typeof meta.provider === 'string' ? meta.provider : undefined,
  }
}

// ---------------------------------------------------------------------------
// statusTagType
// ---------------------------------------------------------------------------

/**
 * Map a facts/signals status string to an Element Plus tag type.
 * complete → success, degraded → warning, non_decisionable → danger, unsupported → info.
 */
export function statusTagType(status: string): 'success' | 'warning' | 'danger' | 'info' {
  switch (status) {
    case 'complete': return 'success'
    case 'degraded': return 'warning'
    case 'non_decisionable': return 'danger'
    case 'unsupported': return 'info'
    default: return 'info'
  }
}

// ---------------------------------------------------------------------------
// reliabilityTagType
// ---------------------------------------------------------------------------

/**
 * Map a reliability string to an Element Plus tag type.
 * high → success, medium → warning, low/estimated → info.
 */
export function reliabilityTagType(reliability: string): 'success' | 'warning' | 'info' {
  switch (reliability) {
    case 'high': return 'success'
    case 'medium': return 'warning'
    default: return 'info'
  }
}
