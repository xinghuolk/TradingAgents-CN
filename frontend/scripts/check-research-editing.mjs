import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'
import * as marked from 'marked'

const timers = new Map()
let nextTimer = 0
function load(path) {
  const file = new URL(path, import.meta.url)
  if (!existsSync(file)) return {}
  const code = ts.transpileModule(readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
  }).outputText
  const exports = {}
  const require = createRequire(import.meta.url)
  runInNewContext(code, {
    exports,
    require: name => (name === 'marked' ? marked : require(name)),
    URL,
    setTimeout: callback => {
      timers.set(++nextTimer, callback)
      return nextTimer
    },
    clearTimeout: id => timers.delete(id)
  })
  return exports
}

const { useResearchAutosave } = load('../src/composables/useResearchAutosave.ts')
assert.equal(typeof useResearchAutosave, 'function', 'autosave must be implemented')

function deferred() {
  let resolve
  let reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

{
  const first = deferred()
  const values = []
  const saver = useResearchAutosave(async value => {
    values.push(value)
    if (values.length === 1) await first.promise
    return 'older server content must never replace local content'
  })
  saver.schedule('one')
  const pending = saver.flush()
  saver.schedule('two')
  saver.schedule('latest')
  const joined = saver.flush()
  assert.deepEqual(values, ['one'], 'concurrent flushes must share one in-flight write')
  assert.notEqual(saver.state.value, 'saved')
  first.resolve()
  assert.equal(await pending, true)
  assert.equal(await joined, true)
  assert.deepEqual(values, ['one', 'latest'], 'queued edits coalesce to the newest value')
  assert.equal(saver.state.value, 'saved')
  assert.equal(timers.size, 0, 'flush clears the pending debounce')
}

{
  const first = deferred()
  const values = []
  const saver = useResearchAutosave(async value => {
    values.push(value)
    if (values.length === 1) await first.promise
    return value
  })
  saver.schedule('old')
  const pending = saver.flush()
  saver.schedule('retained latest')
  first.reject(new Error('offline'))
  assert.equal(await pending, false, 'failed flush blocks record/route transitions')
  assert.equal(saver.state.value, 'failed')
  assert.equal(await saver.retry(), true)
  assert.deepEqual(values, ['old', 'retained latest'])
  assert.equal(saver.state.value, 'saved')
}

{
  const values = []
  const saver = useResearchAutosave(async value => {
    values.push(value)
    return value
  })
  saver.schedule('discard intermediate')
  saver.schedule('')
  assert.equal(timers.size, 1, 'rapid input resets debounce')
  const callback = [...timers.values()][0]
  timers.clear()
  await callback()
  await saver.flush()
  assert.deepEqual(values, [''], 'empty content is still saved')
  saver.dispose()
}

const { renderResearchMarkdown } = load('../src/utils/researchMarkdown.ts')
assert.equal(typeof renderResearchMarkdown, 'function', 'safe Markdown must be implemented')
const rendered = renderResearchMarkdown(
  '# Thesis\n\n**Strong**\n\n<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>'
)
assert.match(rendered, /<h1>Thesis<\/h1>/)
assert.match(rendered, /<strong>Strong<\/strong>/)
assert.doesNotMatch(rendered, /<script|<img/i)
assert.match(rendered, /&lt;script&gt;/)
for (const url of [
  'javascript:alert(1)',
  'data:text/html,evil',
  'jav&#x61;script:alert(1)',
  'vbscript:evil'
]) {
  assert.doesNotMatch(renderResearchMarkdown(`[bad](${url})`), /<a\s/i)
  assert.doesNotMatch(renderResearchMarkdown(`![bad](${url})`), /<img\s/i)
}
assert.match(
  renderResearchMarkdown('[Source](https://example.com "source")'),
  /rel="noopener noreferrer"/
)
assert.match(renderResearchMarkdown('[Source](https://example.com)'), /target="_blank"/)
assert.match(
  renderResearchMarkdown('![Chart](https://example.com/chart.png)'),
  /<img src="https:\/\/example.com\/chart.png"/
)
assert.doesNotMatch(
  renderResearchMarkdown('[a](https://example.com "\" onmouseover=\"evil")'),
  /title="" onmouseover/
)
console.log(
  'PASS: serialized autosave, latest-value retention, failed-save retry, debounce, empty saves, safe Markdown'
)
