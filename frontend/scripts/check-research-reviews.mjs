import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as vue from 'vue'

const require = createRequire(import.meta.url)
const root = new URL('../src/', import.meta.url)
function evaluate(source, dependencies) {
  const exports = {}
  runInNewContext(
    ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
    }).outputText,
    { exports, require: dependencies, setTimeout, clearTimeout }
  )
  return exports
}
const autosaveModule = evaluate(
  readFileSync(new URL('composables/useResearchAutosave.ts', root), 'utf8'),
  require
)
const thesis = {
  security_id: 'A:600519',
  body: 'Original thesis',
  risks: ['risk'],
  current_revision: 0
}

{
  const { app, calls } = setup('ReviewEditor', {
    securityId: 'A:600519',
    entry: {
      id: 'review-b',
      status: 'confirmed',
      security_id: 'HK:00700',
      scope: 'stock',
      references: [],
      scope_metadata: {}
    }
  })
  await app.openDiff()
  assert.equal(
    calls.length,
    0,
    'a retargeted review cannot open another security thesis from the current stock workspace'
  )
  app.diffVisible.value = true
  await app.applyDiff()
  assert.equal(calls.length, 0, 'a mismatched review cannot apply even through the command handler')
}

{
  const { app, calls } = setup('ReviewEditor', {})
  app.securities.value = [
    { security_id: 'A:600519', has_real_holding: true, has_paper_holding: false },
    { security_id: 'HK:00700', has_real_holding: false, has_paper_holding: true }
  ]
  await app.saveDraft()
  assert.deepEqual(Array.from(calls[0][1].security_ids), ['A:600519'])
  app.form.scope_metadata.include_real_holdings = false
  app.form.scope_metadata.include_paper_holdings = true
  app.contextChanged()
  await app.saveDraft()
  const patch = calls.find(call => call[0] === 'patch')[2]
  assert.deepEqual(
    patch.security_ids && Array.from(patch.security_ids),
    ['HK:00700'],
    'draft context patch carries the new associations'
  )
  assert.equal(patch.scope_metadata.include_real_holdings, false)
  assert.equal(patch.scope_metadata.include_paper_holdings, true)
}
{
  const { app, calls } = setup('ReviewEditor', {
    entry: {
      id: 'existing-draft',
      status: 'draft',
      scope: 'portfolio',
      security_ids: ['A:600519'],
      references: [],
      scope_metadata: { include_real_holdings: true }
    }
  })
  app.form.body = 'Body edit while context sources are not loaded'
  app.changed()
  await app.saveDraft()
  assert.equal(
    calls[0][2].security_ids,
    undefined,
    'unrelated edits preserve persisted associations while sources load'
  )
}
function setup(name, props = {}, overrides = {}) {
  const calls = []
  const events = []
  const api = {
    async createEntry(input) {
      calls.push(['create', input])
      return { data: { ...input, id: 'new-1', status: 'draft', current_revision: 0 } }
    },
    async patchEntry(id, input) {
      calls.push(['patch', id, input])
      return {
        data: {
          ...props.entry,
          ...input,
          id,
          current_revision: (props.entry?.current_revision || 0) + 1
        }
      }
    },
    async confirmEntry(id) {
      calls.push(['confirm', id])
      return { data: { id, status: 'confirmed', current_revision: 1, thesis_snapshot: thesis } }
    },
    async getWorkspace() {
      calls.push(['workspace'])
      return { data: { ...thesis } }
    },
    async listReferences() {
      return { data: [] }
    },
    async listEntries() {
      return { data: { items: [], total: 0 } }
    },
    async listRevisions() {
      calls.push(['revisions'])
      return { data: [{ revision: 1 }] }
    },
    async recommendTradeLinks() {
      return { data: [] }
    },
    async applyReviewToThesis(id, patch) {
      calls.push(['apply', id, patch])
      return { data: { revision: 1 } }
    },
    ...overrides
  }
  const { descriptor, errors } = parse(
    readFileSync(new URL(`components/Research/${name}.vue`, root), 'utf8')
  )
  assert.deepEqual(errors, [])
  assert.deepEqual(
    compileTemplate({ source: descriptor.template.content, filename: name, id: name }).errors,
    []
  )
  const compiled = compileScript(descriptor, { id: name }).content
  const module = evaluate(compiled, dep => {
    if (dep === 'vue') return { ...vue, onMounted() {}, watch() {} }
    if (dep === 'element-plus') return { ElMessage: { warning() {}, error() {}, success() {} } }
    if (dep === '@/api/stockResearch') return { stockResearchApi: api }
    if (dep === '@/composables/useResearchAutosave') return autosaveModule
    if (dep.startsWith('@/components/') || dep.startsWith('./')) return {}
    return require(dep)
  })
  return {
    app: module.default.setup(props, { expose() {}, emit: (...args) => events.push(args) }),
    calls,
    events
  }
}
{
  const { app, calls } = setup('DecisionEditor', { securityId: 'A:600519' })
  app.form.decision_action = 'buy'
  app.form.decision_date = '2026-09-08'
  await app.prepareConfirm()
  assert.equal(calls[0][0], 'create')
  assert.equal(calls[0][1].decision_action, 'buy')
  assert.equal(calls[0][1].decision_date, '2026-09-08')
  assert.equal(calls[0][1].body, '')
  assert.equal(
    calls.some(call => call[0] === 'confirm'),
    false,
    'preview does not confirm'
  )
  assert.equal(app.snapshot.value.body, 'Original thesis')
  await app.confirmDecision()
  assert.equal(app.readonly.value, true)
  app.form.body = 'cannot overwrite'
  app.changed()
  await app.flush()
  assert.equal(
    calls.filter(call => call[0] === 'patch').length,
    0,
    'confirmed decisions never autosave'
  )
}
{
  const { app, calls } = setup('ReviewEditor', {})
  assert.equal(app.form.review_kind, 'routine')
  assert.equal(app.form.scope, 'portfolio')
  assert.equal(app.form.scope_metadata.include_market, true)
  assert.equal(app.form.scope_metadata.include_real_holdings, true)
  assert.equal(app.form.scope_metadata.include_paper_holdings, false)
  assert.equal(
    app.form.body,
    '## 市场与持仓表现\n\n## 重要事实、公告和调研变化\n\n## 当前论点变化\n\n## 后续观察\n'
  )
  app.form.scope_metadata.include_real_holdings = false
  await app.confirmReview()
  assert.equal(calls[0][1].decision_id, undefined)
  assert.equal(calls[0][1].scope_metadata.include_real_holdings, false)
  assert.equal(app.record.value.current_revision, 1)
  assert.equal(
    calls.some(call => call[0] === 'apply'),
    false
  )
}
{
  const entry = {
    id: 'review-1',
    status: 'confirmed',
    scope: 'stock',
    security_id: 'A:600519',
    review_kind: 'decision',
    body: 'Formal review',
    references: [],
    scope_metadata: {},
    current_revision: 1
  }
  const { app, calls } = setup('ReviewEditor', { entry, securityId: 'A:600519' })
  app.startRevision()
  app.form.body = 'Revised review'
  app.changed()
  assert.equal(await app.flush(), false, 'navigation cannot silently save a formal revision')
  assert.equal(calls.length, 0)
  await app.saveRevision()
  assert.equal(calls.filter(call => call[0] === 'patch').length, 1)
  assert.equal(
    calls.find(call => call[0] === 'patch')[2].security_ids,
    undefined,
    'formal revisions cannot retarget associations'
  )
  assert.equal(app.record.value.current_revision, 2)
  await app.openDiff()
  assert.equal(
    calls.some(call => call[0] === 'apply'),
    false
  )
  app.proposedBody.value = 'Accepted thesis'
  app.diffVisible.value = false
  assert.equal(
    calls.some(call => call[0] === 'apply'),
    false,
    'cancel performs no write'
  )
  await app.openDiff()
  app.proposedBody.value = 'Accepted thesis'
  await app.applyDiff()
  assert.deepEqual(JSON.parse(JSON.stringify(calls.find(call => call[0] === 'apply'))), [
    'apply',
    'review-1',
    { body: 'Accepted thesis' }
  ])
  assert.ok(calls.filter(call => call[0] === 'workspace').length >= 2)
  assert.ok(calls.some(call => call[0] === 'revisions'))
}
{
  const { app, calls } = setup('ReviewEditor', {
    entry: {
      id: 'archived',
      status: 'archived',
      body: 'Archived',
      references: [],
      scope_metadata: {}
    }
  })
  app.changed()
  await app.confirmReview()
  await app.saveRevision()
  await app.openDiff()
  assert.equal(calls.length, 0, 'archived reviews stay read-only')
}
{
  const real = {
    kind: 'real_trade',
    source_id: 'same-id',
    account_type: 'real',
    label: 'Trade',
    available: true
  }
  const paper = {
    kind: 'paper_trade',
    source_id: 'same-id',
    account_type: 'paper',
    label: 'Trade',
    available: true
  }
  const missing = { kind: 'analysis_report', source_id: 'missing', label: 'Report' }
  const { app, events } = setup(
    'ResearchReferencePicker',
    { modelValue: [missing], securityId: 'A:600519' },
    {
      async listReferences() {
        return { data: [real, paper] }
      }
    }
  )
  await app.load()
  assert.equal(app.groups.value[0].label, '真实账户')
  assert.equal(app.groups.value[1].label, '模拟账户')
  assert.equal(app.options.value.find(item => item.source_id === 'missing').available, false)
  app.select([app.key(real), app.key(paper), app.key(missing)])
  assert.equal(events[0][1].length, 3, 'real and paper source IDs never collide')
}
{
  const { app, calls } = setup('ReviewEditor', { securityId: 'A:600519', reviewKind: 'decision' })
  app.decisions.value = [
    {
      id: 'decision-1',
      security_id: 'A:600519',
      title: 'Buy',
      references: [{ kind: 'real_trade', source_id: 'trade-1', account_type: 'real' }]
    }
  ]
  app.form.decision_id = 'decision-1'
  app.selectDecision()
  await app.confirmReview()
  assert.equal(calls[0][1].review_kind, 'decision')
  assert.equal(calls[0][1].decision_id, 'decision-1')
  assert.equal(calls[0][1].references.length, 2)
  assert.equal(
    calls.some(call => call[0] === 'apply'),
    false
  )
}
console.log(
  'PASS: minimal decision preview/confirmation, review defaults, explicit revisions, diff cancel/apply, archived boundaries, real/paper and unavailable references, decision context'
)
