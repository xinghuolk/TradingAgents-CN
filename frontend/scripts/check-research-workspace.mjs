import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as vue from 'vue'

const require = createRequire(import.meta.url)
const root = new URL('../src/', import.meta.url)
const { descriptor, errors } = parse(
  readFileSync(new URL('views/Research/Workspace.vue', root), 'utf8')
)
assert.deepEqual(errors, [], 'workspace must parse')
const template = compileTemplate({
  source: descriptor.template.content,
  filename: 'Workspace.vue',
  id: 'workspace-boundary-check',
  compilerOptions: { expressionPlugins: ['typescript'] }
})
assert.deepEqual(template.errors, [], 'workspace template must compile')
const compiled = compileScript(descriptor, { id: 'workspace-boundary-check' }).content
function evaluate(source, dependencies) {
  const exports = {}
  runInNewContext(
    ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
    }).outputText,
    {
      exports,
      require: dependencies,
      window: { addEventListener() {}, removeEventListener() {} },
      setTimeout,
      clearTimeout
    }
  )
  return exports
}
const { useResearchAutosave } = evaluate(
  readFileSync(new URL('composables/useResearchAutosave.ts', root), 'utf8'),
  require
)
const { render } = evaluate(template.code, name =>
  name === 'vue'
    ? {
        ...vue,
        resolveComponent: name => ({ name }),
        resolveDirective: () => undefined,
        withDirectives: node => node
      }
    : require(name)
)
function renderedCommands(app) {
  const commands = []
  function visit(node) {
    if (!node || typeof node !== 'object') return
    if (Array.isArray(node)) {
      node.forEach(visit)
      return
    }
    if (node.props?.command && !node.props.disabled) commands.push(node.props.command)
    if (Array.isArray(node.children)) visit(node.children)
    else if (node.children && typeof node.children === 'object') {
      Object.values(node.children)
        .filter(value => typeof value === 'function')
        .forEach(slot => visit(slot()))
    }
  }
  visit(render(vue.proxyRefs(app), []))
  return commands
}
const workspace = {
  security_id: 'A:000001',
  market: 'CN',
  code: '000001',
  name: '平安银行',
  body: 'existing thesis',
  assumptions: [],
  risks: [],
  invalidation_conditions: [],
  open_questions: [],
  tags: [],
  external_links: [],
  current_revision: 0,
  created_at: '2026-09-08T00:00:00Z',
  updated_at: '2026-09-08T00:00:00Z'
}
const entry = {
  id: 'note-1',
  entry_type: 'note',
  scope: 'stock',
  security_id: 'A:000001',
  security_ids: ['A:000001'],
  title: 'Original',
  body: 'preserved body',
  status: 'draft',
  tags: [],
  external_links: [],
  references: [],
  topic: null,
  conclusion: null,
  decision_action: null,
  decision_date: null,
  planned_price: null,
  target_allocation: null,
  horizon: null,
  thesis_snapshot: null,
  review_kind: null,
  decision_id: null,
  scope_metadata: {},
  ai_drafts: [],
  source_metadata: {},
  warnings: [],
  current_revision: 0,
  confirmed_at: null,
  archived_at: null,
  deleted_at: null,
  created_at: workspace.created_at,
  updated_at: workspace.updated_at
}
function setup(market = 'CN', overrides = {}) {
  const calls = []
  const guards = []
  const route = { params: { code: '000001' }, query: market === undefined ? {} : { market } }
  const api = {
    async createWorkspace(input) {
      calls.push(['workspace', input])
      return { data: structuredClone(workspace) }
    },
    async patchWorkspace(id, patch) {
      calls.push(['save', id, patch])
      return { data: { ...workspace, ...patch } }
    },
    async listEntries(query) {
      calls.push(['list', query])
      return { data: { items: [structuredClone(entry)], total: 1 } }
    },
    async getEntry() {
      return { data: structuredClone(entry) }
    },
    async listReferences() {
      return { data: [] }
    },
    async convertEntry(id, target) {
      calls.push(['convert', id, target])
      return { data: { ...entry, entry_type: target } }
    },
    async archiveEntry() {
      return { data: { ...entry, status: 'archived' } }
    },
    ...overrides
  }
  const module = evaluate(compiled, name => {
    if (name === 'vue') return { ...vue, watch() {}, onBeforeUnmount() {} }
    if (name === 'vue-router')
      return {
        useRoute: () => route,
        useRouter: () => ({}),
        onBeforeRouteLeave: guard => guards.push(guard),
        onBeforeRouteUpdate: guard => guards.push(guard)
      }
    if (name === 'element-plus')
      return {
        ElMessage: { warning() {}, error() {}, success() {} },
        ElMessageBox: { confirm: async () => {} }
      }
    if (name === '@/api/stockResearch') return { stockResearchApi: api }
    if (name === '@/composables/useResearchAutosave') return { useResearchAutosave }
    if (name === '@/utils/datetime') return { formatDateTime: value => value }
    if (name.startsWith('@/components/')) return {}
    return require(name)
  })
  return { app: module.default.setup({}, { expose() {} }), calls, guards, route }
}

for (const market of [null, 'invalid', ['CN', 'US']]) {
  const { app, calls } = setup(market)
  await app.loadWorkspace()
  assert.equal(app.market.value, null)
  assert.equal(calls.length, 0, 'missing or invalid market must not load/create a workspace')
}
{
  const { app, calls } = setup()
  await app.loadWorkspace()
  app.workspace.value.body = 'latest thesis'
  app.scheduleWorkspace()
  await app.switchSection('note')
  assert.equal(calls[1][0], 'save', 'section navigation flushes before loading entries')
  assert.equal(calls[1][1], 'A:000001', 'writes use the canonical server security ID')
  assert.equal(calls[1][2].body, 'latest thesis')
  assert.equal(calls[2][0], 'list')
  assert.equal(app.section.value, 'note')
  app.autosave.dispose()
}
{
  const { app, guards } = setup('CN', {
    async patchWorkspace() {
      throw new Error('offline')
    }
  })
  await app.loadWorkspace()
  app.workspace.value.body = 'retained unsaved text'
  app.scheduleWorkspace()
  await app.switchSection('note')
  assert.equal(app.section.value, 'thesis', 'failed saves block section changes')
  assert.equal(app.workspace.value.body, 'retained unsaved text')
  assert.equal(await guards[0](), false, 'failed saves block route leave')
  assert.equal(await guards[1](), false, 'failed saves block route parameter changes')
  app.autosave.dispose()
}
{
  const { app, calls } = setup()
  await app.loadWorkspace()
  await app.openEntry(entry)
  app.section.value = 'note'
  assert.ok(renderedCommands(app).includes('convert'), 'active document offers conversion')
  await app.entryAction('convert')
  assert.equal(app.entry.value.id, 'note-1')
  assert.equal(app.entry.value.body, 'preserved body')
  assert.equal(app.section.value, 'research')
  assert.equal(calls.filter(call => call[0] === 'convert').length, 1)
  await app.entryAction('archive')
  assert.equal(app.editableEntry.value, false, 'archived entries cannot be autosaved')
  assert.ok(
    !renderedCommands(app).includes('convert'),
    'archived document must not offer conversion'
  )
  await app.entryAction('convert')
  assert.equal(
    calls.filter(call => call[0] === 'convert').length,
    1,
    'archived conversion handler cannot send a request'
  )
  app.autosave.dispose()
}
{
  const { app } = setup('CN', {
    async listEntries(query) {
      return { data: { items: query.page === 1 ? [structuredClone(entry)] : [], total: 20 } }
    }
  })
  await app.loadWorkspace()
  app.section.value = 'note'
  app.entryPage.value = 2
  await app.loadEntries()
  assert.equal(
    app.entryPage.value,
    1,
    'deleting the final row on a page returns to an available page'
  )
  assert.equal(app.entries.value[0].id, 'note-1')
}
{
  let resolveOlder
  let count = 0
  const { app, route } = setup('CN', {
    async createWorkspace() {
      if (++count === 1)
        return new Promise(resolve => {
          resolveOlder = resolve
        })
      return { data: { ...workspace, market: 'US', security_id: 'US:TEST', code: 'TEST' } }
    }
  })
  const older = app.loadWorkspace()
  route.query.market = 'US'
  route.params.code = 'TEST'
  await app.loadWorkspace()
  resolveOlder({ data: structuredClone(workspace) })
  await older
  assert.equal(
    app.workspace.value.security_id,
    'US:TEST',
    'stale workspace responses cannot replace another security'
  )
}
{
  let calls = 0
  const { app } = setup('CN', {
    async listEntries() {
      calls += 1
      throw new Error('offline')
    }
  })
  await app.loadWorkspace()
  await app.switchSection('note')
  assert.equal(
    app.entriesError.value,
    true,
    'failed list fetch has an error state rather than an empty result'
  )
  await app.changePage(1)
  assert.equal(calls, 2, 'list failures have a working retry path')
}
{
  const events = []
  const revision = {
    id: 'rev-1',
    target_type: 'workspace',
    target_id: 'A:000001',
    revision: 1,
    snapshot: { ...workspace, label: 'Checkpoint' },
    reason: 'manual',
    created_at: workspace.updated_at
  }
  const { app } = setup('CN', {
    async patchWorkspace(id, patch) {
      events.push(['draft', id, patch.body])
      return { data: workspace }
    },
    async listRevisions(type, id) {
      events.push(['history', type, id])
      return { data: [revision] }
    },
    async restoreRevision(id) {
      events.push(['restore-version', id])
      return { data: { ...revision, revision: 2 } }
    },
    async getWorkspace() {
      return { data: { ...workspace, body: 'restored', current_revision: 2 } }
    },
    async listTrash() {
      return { data: { items: [], total: 0 } }
    },
    async restoreTrashEntry(id) {
      events.push(['restore-trash', id])
      return { data: entry }
    },
    async deleteTrashEntry(id) {
      events.push(['permanent-delete', id])
    }
  })
  await app.loadWorkspace()
  app.updateWorkspaceBody('before history')
  await app.openVersions()
  assert.deepEqual(events.slice(0, 2), [
    ['draft', 'A:000001', 'before history'],
    ['history', 'workspace', 'A:000001']
  ])
  await app.restoreVersion()
  assert.equal(app.workspace.value.body, 'restored')
  assert.equal(app.workspace.value.current_revision, 2)
  assert.ok(events.some(event => event[0] === 'restore-version' && event[1] === 'rev-1'))
  await app.restoreTrash(entry)
  await app.permanentlyDelete(entry)
  assert.deepEqual(events.slice(-2), [
    ['restore-trash', 'note-1'],
    ['permanent-delete', 'note-1']
  ])
}
console.log(
  'PASS: explicit market, canonical IDs, flush-before-switch, failed-save navigation guards, conversion, archived read-only, empty-page recovery'
)
