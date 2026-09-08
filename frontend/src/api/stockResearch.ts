import { ApiClient } from './request'

export type ResearchMarket = 'CN' | 'HK' | 'US'
export type ResearchEntryType = 'note' | 'research' | 'decision' | 'review'
export type ResearchEntryStatus = 'draft' | 'confirmed' | 'archived'
export type ResearchScope = 'stock' | 'portfolio'
export type DecisionAction = 'buy' | 'add' | 'reduce' | 'sell' | 'observe'
export type ReviewKind = 'routine' | 'decision'
export type ReferenceKind =
  | 'analysis_report'
  | 'real_trade'
  | 'paper_trade'
  | 'decision'
  | 'holding_date'

export interface ResearchPage<T> {
  items: T[]
  page: number
  page_size: number
  total: number
}

export interface ResearchWorkspaceSummary {
  security_id: string
  market: ResearchMarket
  code: string
  name: string
  thesis_summary: string
  updated_at: string
  latest_entry_type: ResearchEntryType | null
  latest_entry_at: string | null
  has_real_holding: boolean
  has_paper_holding: boolean
  watchlisted: boolean
}

export interface ResearchWorkspace {
  security_id: string
  market: ResearchMarket
  code: string
  name: string
  body: string
  assumptions: string[]
  risks: string[]
  invalidation_conditions: string[]
  open_questions: string[]
  tags: string[]
  external_links: string[]
  current_revision: number
  created_at: string
  updated_at: string
}

export interface ResearchReference {
  kind: ReferenceKind
  source_id: string
  account_type?: 'real' | 'paper' | null
  source_date?: string | null
  label?: string | null
}

export interface ResearchEntry {
  id: string
  entry_type: ResearchEntryType
  scope: ResearchScope
  security_id: string | null
  security_ids: string[]
  title: string
  body: string
  status: ResearchEntryStatus
  tags: string[]
  external_links: string[]
  references: ResearchReference[]
  topic: string | null
  conclusion: string | null
  decision_action: DecisionAction | null
  decision_date: string | null
  planned_price: string | null
  target_allocation: string | null
  horizon: string | null
  thesis_snapshot: Record<string, unknown> | null
  review_kind: ReviewKind | null
  decision_id: string | null
  scope_metadata: Record<string, unknown>
  ai_drafts: ResearchAIDraft[]
  source_metadata: Record<string, unknown>
  warnings: string[]
  current_revision: number
  confirmed_at: string | null
  archived_at: string | null
  deleted_at: string | null
  created_at: string
  updated_at: string
}

export interface ResearchRevision {
  id: string
  target_type: 'workspace' | 'entry'
  target_id: string
  revision: number
  snapshot: Record<string, unknown>
  reason: string
  created_at: string
}

export interface ResearchReferenceCandidate extends ResearchReference {
  security_id: string
  label: string
  available: boolean
  snapshot: Record<string, unknown>
}

export interface WorkspaceQuery {
  market?: ResearchMarket
  query?: string
  real_holding?: boolean
  paper_holding?: boolean
  watchlisted?: boolean
  page?: number
  page_size?: number
}

export interface EntryQuery {
  security_id?: string
  entry_type?: ResearchEntryType
  status?: ResearchEntryStatus
  scope?: ResearchScope
  query?: string
  page?: number
  page_size?: number
}

export interface CreateWorkspaceInput {
  market: ResearchMarket
  code: string
  name: string
}

export interface WorkspacePatchInput {
  body?: string
  assumptions?: string[]
  risks?: string[]
  invalidation_conditions?: string[]
  open_questions?: string[]
  tags?: string[]
  external_links?: string[]
}

export interface CreateEntryInput {
  entry_type: ResearchEntryType
  scope?: ResearchScope
  security_id?: string
  security_ids?: string[]
  title?: string
  body?: string
  tags?: string[]
  external_links?: string[]
  references?: ResearchReference[]
  topic?: string
  conclusion?: string
  decision_action?: DecisionAction
  decision_date?: string
  planned_price?: string
  target_allocation?: string
  horizon?: string
  review_kind?: ReviewKind
  decision_id?: string
  scope_metadata?: Record<string, unknown>
}

export type EntryPatchInput = Partial<
  Omit<CreateEntryInput, 'entry_type' | 'scope' | 'security_id'>
>

export interface ReferenceQuery {
  security_id: string
  date_from?: string
  date_through?: string
}

export interface ResearchAIDraft {
  content: string
  provider: string
  model_name: string
  reasoning_effort: string | null
  generated_at: string | null
  prompt_version: string
  task_id: string
  source_ids: string[]
  references: Array<ResearchReference & { available?: boolean }>
}

export interface GenerationTask {
  id: string
  target_entry_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  provider: string
  model_name: string
  reasoning_effort: string | null
  prompt_version: string
  source_ids: string[]
  references: ResearchReference[]
  generated_at: string | null
  content: string | null
  error_message: string | null
}

export interface GenerationTaskInput {
  target_entry_id: string
  draft_kind: ResearchEntryType
  provider: string
  model_name: string
  reasoning_effort: string | null
  references: ResearchReference[]
}

export const stockResearchApi = {
  createGenerationTask: (input: GenerationTaskInput) =>
    ApiClient.post<GenerationTask>('/api/research/generation-tasks', input, { retryCount: 0 }),
  getGenerationTask: (taskId: string) =>
    ApiClient.get<GenerationTask>(
      `/api/research/generation-tasks/${encodeURIComponent(taskId)}`,
      undefined,
      { retryCount: 0 }
    ),
  listWorkspaces: (query: WorkspaceQuery = {}) =>
    ApiClient.get<ResearchPage<ResearchWorkspaceSummary>>('/api/research/workspaces', query),
  createWorkspace: (input: CreateWorkspaceInput) =>
    ApiClient.post<ResearchWorkspace>('/api/research/workspaces', input),
  getWorkspace: (securityId: string) =>
    ApiClient.get<ResearchWorkspace>(`/api/research/workspaces/${encodeURIComponent(securityId)}`),
  patchWorkspace: (securityId: string, input: WorkspacePatchInput) =>
    ApiClient.patch<ResearchWorkspace>(
      `/api/research/workspaces/${encodeURIComponent(securityId)}`,
      input
    ),
  saveWorkspaceVersion: (securityId: string, label = '') =>
    ApiClient.post<ResearchRevision>(
      `/api/research/workspaces/${encodeURIComponent(securityId)}/revisions`,
      { label }
    ),
  listEntries: (query: EntryQuery = {}) =>
    ApiClient.get<ResearchPage<ResearchEntry>>('/api/research/entries', query),
  createEntry: (input: CreateEntryInput) =>
    ApiClient.post<ResearchEntry>('/api/research/entries', input),
  getEntry: (entryId: string) =>
    ApiClient.get<ResearchEntry>(`/api/research/entries/${encodeURIComponent(entryId)}`),
  patchEntry: (entryId: string, input: EntryPatchInput) =>
    ApiClient.patch<ResearchEntry>(`/api/research/entries/${encodeURIComponent(entryId)}`, input),
  convertEntry: (entryId: string, target: 'note' | 'research', topic?: string) =>
    ApiClient.post<ResearchEntry>(`/api/research/entries/${encodeURIComponent(entryId)}/convert`, {
      target,
      topic
    }),
  confirmEntry: (entryId: string) =>
    ApiClient.post<ResearchEntry>(`/api/research/entries/${encodeURIComponent(entryId)}/confirm`),
  applyReviewToThesis: (entryId: string, patch: WorkspacePatchInput) =>
    ApiClient.post<ResearchRevision>(
      `/api/research/entries/${encodeURIComponent(entryId)}/apply-to-thesis`,
      patch
    ),
  archiveEntry: (entryId: string) =>
    ApiClient.post<ResearchEntry>(`/api/research/entries/${encodeURIComponent(entryId)}/archive`),
  deleteEntry: (entryId: string) =>
    ApiClient.delete<void>(`/api/research/entries/${encodeURIComponent(entryId)}`),
  listRevisions: (targetType: 'workspace' | 'entry', targetId: string) =>
    ApiClient.get<ResearchRevision[]>('/api/research/revisions', {
      target_type: targetType,
      target_id: targetId
    }),
  getRevision: (revisionId: string) =>
    ApiClient.get<ResearchRevision>(`/api/research/revisions/${encodeURIComponent(revisionId)}`),
  restoreRevision: (revisionId: string) =>
    ApiClient.post<ResearchRevision>(
      `/api/research/revisions/${encodeURIComponent(revisionId)}/restore`
    ),
  listTrash: (query: Pick<EntryQuery, 'page' | 'page_size'> = {}) =>
    ApiClient.get<ResearchPage<ResearchEntry>>('/api/research/trash', query),
  restoreTrashEntry: (entryId: string) =>
    ApiClient.post<ResearchEntry>(`/api/research/trash/${encodeURIComponent(entryId)}/restore`),
  deleteTrashEntry: (entryId: string) =>
    ApiClient.delete<void>(`/api/research/trash/${encodeURIComponent(entryId)}`),
  listReferences: (query: ReferenceQuery) =>
    ApiClient.get<ResearchReferenceCandidate[]>('/api/research/references', query),
  recommendTradeLinks: (decisionId: string) =>
    ApiClient.get<ResearchReferenceCandidate[]>('/api/research/links/recommendations', {
      decision_id: decisionId
    }),
  setDecisionTradeLinks: (decisionId: string, references: ResearchReference[]) =>
    ApiClient.put<ResearchEntry>(
      `/api/research/links/decisions/${encodeURIComponent(decisionId)}`,
      {
        references
      }
    ),
  deleteDecisionTradeLink: (
    decisionId: string,
    kind: 'real_trade' | 'paper_trade',
    sourceId: string
  ) =>
    ApiClient.delete<ResearchEntry>(
      `/api/research/links/decisions/${encodeURIComponent(decisionId)}/${kind}/${encodeURIComponent(sourceId)}`
    )
}
