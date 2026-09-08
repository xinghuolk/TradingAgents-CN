import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as vue from 'vue'

const require = createRequire(import.meta.url)
const source = readFileSync(
  new URL('../src/components/Research/GenerationDialog.vue', import.meta.url),
  'utf8'
)
const { descriptor } = parse(source)
assert.deepEqual(
  compileTemplate({
    source: descriptor.template.content,
    filename: 'GenerationDialog.vue',
    id: 'generation'
  }).errors,
  []
)
const code = ts.transpileModule(compileScript(descriptor, { id: 'generation' }).content, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
}).outputText
const configs = [
  { provider: 'openai', model_name: 'reasoning-exact', enabled: true, features: ['reasoning'] },
  { provider: 'codex', model_name: 'gpt-exact', enabled: true, features: ['reasoning'] }
]
const entryReferences = [
  { kind: 'real_trade', source_id: 'trade-1', account_type: 'real', label: 'Trade' }
]
const frozenReferences = [
  {
    kind: 'analysis_report',
    source_id: 'report-1',
    account_type: null,
    source_date: '2026-09-08',
    label: 'Generation-only report'
  }
]
const frozen = {
  id: 'failed-1',
  target_entry_id: 'entry-1',
  status: 'failed',
  provider: 'openai',
  model_name: 'reasoning-exact',
  reasoning_effort: 'high',
  references: frozenReferences,
  prompt_version: 'research-draft-v1',
  source_ids: ['report-1'],
  generated_at: null,
  content: null,
  error_message: 'Generation failed'
}
const conflictingPreference = { provider: 'codex', model_name: 'gpt-exact', reasoning_effort: null }

function setup({ initialTask, models = configs, preference = conflictingPreference } = {}) {
  const exports = {}
  const calls = []
  const storage = new Map([
    ['user-info', JSON.stringify({ id: 'test' })],
    ['research-generation:test', JSON.stringify(preference)]
  ])
  runInNewContext(code, {
    exports,
    localStorage: {
      getItem: key => storage.get(key),
      setItem: (key, value) => storage.set(key, value)
    },
    setTimeout: () => 1,
    clearTimeout() {},
    require(name) {
      if (name === 'vue') return { ...vue, onMounted() {}, onBeforeUnmount() {} }
      if (name === '@/api/config')
        return {
          configApi: {
            async getLLMConfigs() {
              return models
            }
          }
        }
      if (name === '@/api/stockResearch')
        return {
          stockResearchApi: {
            async createGenerationTask(input) {
              calls.push(JSON.parse(JSON.stringify(input)))
              return { data: { ...frozen, ...input, id: 'retry-1', status: 'pending' } }
            }
          }
        }
      if (name.startsWith('@/components/')) return {}
      return require(name)
    }
  })
  const app = exports.default.setup(
    {
      entry: {
        id: 'entry-1',
        entry_type: 'decision',
        status: 'draft',
        references: entryReferences,
        security_ids: []
      },
      initialTask
    },
    { emit() {}, expose() {} }
  )
  return { app, calls }
}

for (const status of ['pending', 'running', 'failed']) {
  const { app, calls } = setup({ initialTask: { ...frozen, status } })
  await app.loadModels()
  assert.equal(
    app.provider.value,
    'openai',
    `${status}: frozen provider must override local preferences`
  )
  assert.equal(app.modelName.value, 'reasoning-exact')
  assert.equal(app.effort.value, 'high')
  assert.deepEqual(
    JSON.parse(JSON.stringify(app.references.value)),
    frozenReferences,
    `${status}: task-only references must survive reopening`
  )
  assert.equal(app.activeTask.value, status !== 'failed')
  await app.submit()
  if (status !== 'failed') assert.equal(calls.length, 0, 'active tasks cannot be resubmitted')
  else
    assert.deepEqual(
      calls,
      [
        {
          target_entry_id: 'entry-1',
          draft_kind: 'decision',
          provider: 'openai',
          model_name: 'reasoning-exact',
          reasoning_effort: 'high',
          references: frozenReferences
        }
      ],
      'failed retry must send exactly the frozen inputs'
    )
}

for (const models of [
  configs.map(model => ({ ...model, enabled: model.provider !== 'openai' })),
  configs.filter(model => model.provider !== 'openai')
]) {
  const { app, calls } = setup({ initialTask: frozen, models })
  await app.loadModels()
  assert.equal(app.provider.value, 'openai')
  assert.equal(app.modelName.value, 'reasoning-exact', 'unavailable frozen model remains visible')
  assert.equal(app.effort.value, 'high', 'unavailable inputs must not be silently cleared')
  assert.match(app.selectionError.value, /模型.*不可用.*重新选择/)
  await app.submit()
  assert.equal(calls.length, 0, 'unavailable model blocks retry')
  app.provider.value = 'codex'
  app.changeProvider()
  app.modelName.value = 'gpt-exact'
  app.changeModel()
  assert.equal(app.selectionError.value, '')
  await app.submit()
  assert.deepEqual(
    calls,
    [
      {
        target_entry_id: 'entry-1',
        draft_kind: 'decision',
        provider: 'codex',
        model_name: 'gpt-exact',
        reasoning_effort: null,
        references: frozenReferences
      }
    ],
    'only explicit user selection may replace unavailable task inputs'
  )
}

for (const initialTask of [
  frozen,
  { ...frozen, provider: 'codex', model_name: 'gpt-exact' },
  { ...frozen, reasoning_effort: 'unsupported-value' }
]) {
  const { app, calls } = setup({
    initialTask,
    models: configs.map(model => ({ ...model, features: [] }))
  })
  await app.loadModels()
  assert.equal(app.effort.value, initialTask.reasoning_effort)
  assert.match(app.selectionError.value, /推理强度.*不.*支持.*重新选择/)
  await app.submit()
  assert.equal(
    calls.length,
    0,
    'unsupported remembered effort blocks retry instead of becoming null'
  )
  app.effort.value = ''
  assert.equal(app.selectionError.value, '')
  await app.submit()
  assert.equal(calls[0].reasoning_effort, null, 'user may explicitly choose default effort')
}

{
  const { app, calls } = setup({ initialTask: { ...frozen, reasoning_effort: null } })
  await app.loadModels()
  await app.submit()
  assert.equal(calls[0].reasoning_effort, null, 'frozen default effort stays null')
}

{
  const { app } = setup({
    preference: { provider: 'openai', model_name: 'reasoning-exact', reasoning_effort: 'medium' }
  })
  await app.loadModels()
  assert.equal(app.effort.value, 'medium')
  assert.deepEqual(
    JSON.parse(JSON.stringify(app.references.value)),
    entryReferences,
    'fresh dialog still uses entry references'
  )
}

{
  const { app, calls } = setup({
    preference: { provider: 'openai', model_name: 'removed-model', reasoning_effort: 'high' }
  })
  await app.loadModels()
  assert.equal(app.modelName.value, '')
  await app.submit()
  assert.equal(calls.length, 0, 'fresh stale preferences still require explicit selection')
}

console.log(
  'PASS: frozen pending/running/failed inputs, exact retry payload, unavailable model/effort blocking, explicit reselection, fresh preferences/references'
)
