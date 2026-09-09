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
    {
      exports,
      require: dependencies,
      setTimeout,
      clearTimeout,
      window: { addEventListener() {}, removeEventListener() {} },
      localStorage: {
        getItem() {
          return null
        },
        setItem() {}
      }
    }
  )
  return exports
}
const autosave = evaluate(
  readFileSync(new URL('composables/useResearchAutosave.ts', root), 'utf8'),
  require
)
const latest = evaluate(readFileSync(new URL('utils/latestRequest.ts', root), 'utf8'), require)
function setup(path, props = {}, overrides = {}) {
  const mounts = [],
    calls = [],
    messages = []
  const api = {
    async listWorkspaces() {
      return { data: { items: [], total: 0 } }
    },
    async listHoldings() {
      return { data: [] }
    },
    async listEntries() {
      return { data: { items: [], total: 0 } }
    },
    async listReferences() {
      return { data: [] }
    },
    async listRevisions() {
      return { data: [] }
    },
    async createEntry(input) {
      calls.push(['create', input])
      return { data: { ...input, id: 'new', status: 'draft', references: [] } }
    },
    ...overrides
  }
  const { descriptor } = parse(readFileSync(new URL(path, root), 'utf8'))
  assert.deepEqual(
    compileTemplate({
      source: descriptor.template.content,
      filename: path,
      id: path,
      compilerOptions: { expressionPlugins: ['typescript'] }
    }).errors,
    []
  )
  const component = evaluate(compileScript(descriptor, { id: path }).content, name => {
    if (name === 'vue')
      return { ...vue, onMounted: fn => mounts.push(fn), onBeforeUnmount() {}, watch() {} }
    if (name === 'vue-router')
      return {
        useRoute: () => ({ params: { code: '600519' }, query: { market: 'CN' } }),
        useRouter: () => ({ push() {} }),
        onBeforeRouteLeave() {},
        onBeforeRouteUpdate() {}
      }
    if (name === 'element-plus')
      return {
        ElMessage: {
          warning: value => messages.push(value),
          error: value => messages.push(value),
          success() {}
        },
        ElMessageBox: { prompt: async () => ({ value: 'checkpoint' }), confirm: async () => true }
      }
    if (name === '@/api/stockResearch') return { stockResearchApi: api }
    if (name === '@/api/config') return { configApi: { getLLMConfigs: async () => [] } }
    if (name === '@/utils/latestRequest') return latest
    if (name === '@/utils/datetime') return { formatDateTime: value => value }
    if (name === '@/composables/useResearchAutosave') return autosave
    if (name.startsWith('@/components/') || name.startsWith('./')) return {}
    return require(name)
  }).default
  return {
    app: component.setup(props, { expose() {}, emit() {} }),
    calls,
    messages,
    mount: () => Promise.all(mounts.map(fn => fn()))
  }
}

const cases = []
for (const kind of ['note', 'research'])
  cases.push([
    `manual ${kind} version`,
    async () => {
      const saved = []
      const { app } = setup(
        'views/Research/Workspace.vue',
        {},
        {
          async saveEntryVersion(id, label) {
            saved.push([id, label])
            return { data: { revision: 1 } }
          }
        }
      )
      app.workspace.value = { security_id: 'A:600519', current_revision: 0 }
      app.entry.value = {
        id: 'document',
        entry_type: kind,
        status: 'draft',
        references: [],
        body: 'human',
        title: 'title'
      }
      app.section.value = kind
      await app.saveVersion()
      assert.deepEqual(saved, [['document', 'checkpoint']])
      assert.equal(app.entry.value.current_revision, 1)
    }
  ])
cases.push([
  'confirmed decision add/change/unlink and conflict',
  async () => {
    const record = {
      id: 'decision',
      status: 'confirmed',
      references: [],
      body: 'immutable',
      thesis_snapshot: { body: 'frozen' }
    }
    const real = { kind: 'real_trade', source_id: 'r', account_type: 'real', label: 'Real' }
    const paper = { kind: 'paper_trade', source_id: 'p', account_type: 'paper', label: 'Paper' }
    let conflict = false
    const { app } = setup(
      'components/Research/DecisionEditor.vue',
      { entry: record, securityId: 'A:600519' },
      {
        async recommendTradeLinks() {
          return {
            data: [real, paper].map(item => ({
              ...item,
              available: true,
              security_id: 'A:600519',
              snapshot: { quantity: 100 }
            }))
          }
        },
        async setDecisionTradeLinks(id, refs) {
          assert.ok(
            refs.every(
              item => !('snapshot' in item) && !('available' in item) && !('security_id' in item)
            )
          )
          if (conflict) throw { response: { data: { detail: { code: 'RESEARCH_CONFLICT' } } } }
          return { data: { ...record, references: [...refs] } }
        },
        async deleteDecisionTradeLink() {
          return { data: record }
        }
      }
    )
    await app.loadTradeLinks()
    app.selectTradeLinks(app.tradeCandidates.value.map(app.tradeKey))
    await app.saveTradeLinks()
    assert.equal(app.record.value.references.length, 2)
    app.tradeReferences.value = [paper]
    await app.saveTradeLinks()
    assert.equal(app.record.value.references[0].account_type, 'paper')
    await app.unlinkTrade(paper)
    assert.equal(app.record.value.references.length, 0)
    conflict = true
    app.tradeReferences.value = [real]
    await app.saveTradeLinks()
    assert.match(app.linksError.value, /已关联其他决策/)
    assert.equal(app.record.value.body, 'immutable')
    assert.equal(app.record.value.thesis_snapshot.body, 'frozen')
  }
])
cases.push([
  'first-time complete holding associations',
  async () => {
    const holdings = Array.from({ length: 126 }, (_, i) => ({
      security_id: `US:S${i}`,
      account_type: i === 125 ? 'paper' : 'real'
    }))
    const { app, calls, mount } = setup(
      'components/Research/ReviewEditor.vue',
      {},
      {
        async listHoldings() {
          return { data: holdings }
        }
      }
    )
    await mount()
    await app.saveDraft()
    assert.equal(calls[0][1].security_ids.length, 125)
    assert.equal(calls[0][1].security_ids[124], 'US:S124')
    assert.equal(app.securities.value.length, 126)
  }
])
cases.push([
  'global market-only review lifecycle and trash',
  async () => {
    const operations = []
    const review = { id: 'market-only', status: 'draft', security_ids: [] }
    const { app } = setup(
      'views/Research/index.vue',
      {},
      {
        async archiveEntry(id) {
          operations.push(['archive', id])
          return { data: { ...review, status: 'archived' } }
        },
        async deleteEntry(id) {
          operations.push(['delete', id])
        },
        async listTrash() {
          return { data: { items: [review], total: 1 } }
        },
        async restoreTrashEntry(id) {
          operations.push(['restore', id])
        },
        async deleteTrashEntry(id) {
          operations.push(['permanent', id])
        }
      }
    )
    app.activeReview.value = review
    await app.archiveReview()
    await app.deleteReview()
    await app.openTrash()
    assert.equal(app.trashEntries.value[0].id, 'market-only')
    await app.restoreTrash(review)
    await app.permanentlyDeleteTrash(review)
    assert.deepEqual(
      operations.map(item => item[0]),
      ['archive', 'delete', 'restore', 'permanent']
    )
  }
])
cases.push([
  'AI context defaults are visible and adjustable',
  async () => {
    let flushed = false
    const ref = { kind: 'real_trade', source_id: 'r', account_type: 'real', available: true }
    const { app } = setup(
      'components/Research/GenerationDialog.vue',
      {
        entry: { id: 'review', entry_type: 'review', status: 'draft', references: [] },
        beforeSubmit: async () => {
          flushed = true
          return true
        }
      },
      {
        async previewGenerationContext() {
          assert.equal(flushed, true, 'context must read the latest autosaved references and scope')
          return {
            data: {
              options: { include_thesis: true, date_from: '2026-09-01' },
              references: [ref],
              sources: [{ kind: 'market', available: false }],
              theses: [{ security_id: 'A:600519' }],
              recent_entries: [{ title: 'recent' }]
            }
          }
        }
      }
    )
    await app.loadContext()
    assert.equal(app.contextOptions.value.include_thesis, true)
    assert.equal(app.contextOptions.value.date_from, '2026-09-01')
    assert.equal(app.references.value[0].source_id, 'r')
    assert.equal(app.contextPreview.value.sources[0].available, false)
    app.contextOptions.value.include_thesis = false
    assert.equal(app.contextOptions.value.include_thesis, false)
  }
])

let failed = 0
for (const [name, run] of cases) {
  try {
    await run()
    console.log('PASS', name)
  } catch (error) {
    failed++
    console.error('FAIL', name, error.message)
  }
}
assert.equal(failed, 0, `${failed} final-wave behavior checks failed`)
