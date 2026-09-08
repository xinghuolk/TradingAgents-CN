import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

const source = readFileSync(new URL('../src/utils/latestRequest.ts', import.meta.url), 'utf8')
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
}).outputText
const exports = {}
runInNewContext(code, { exports, require: createRequire(import.meta.url) })

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function handlers(events, name) {
  return {
    onSuccess: value => events.push(`${name}:success:${value}`),
    onError: () => events.push(`${name}:error`),
    onSettled: () => events.push(`${name}:settled`)
  }
}

{
  const coordinate = exports.createLatestRequestCoordinator()
  const events = []
  const older = deferred()
  const latest = deferred()
  const olderRun = coordinate(() => older.promise, handlers(events, 'older'))
  const latestRun = coordinate(() => latest.promise, handlers(events, 'latest'))

  latest.resolve('filtered')
  await latestRun
  older.resolve('unfiltered')
  await olderRun

  assert.deepEqual(events, ['latest:success:filtered', 'latest:settled'])
}

{
  const coordinate = exports.createLatestRequestCoordinator()
  const events = []
  const older = deferred()
  const latest = deferred()
  const olderRun = coordinate(() => older.promise, handlers(events, 'older'))
  const latestRun = coordinate(() => latest.promise, handlers(events, 'latest'))

  older.reject(new Error('stale failure'))
  await olderRun
  assert.deepEqual(events, [])
  latest.resolve('filtered')
  await latestRun
  assert.deepEqual(events, ['latest:success:filtered', 'latest:settled'])
}

{
  const coordinate = exports.createLatestRequestCoordinator()
  const events = []
  const latest = deferred()
  const latestRun = coordinate(() => latest.promise, handlers(events, 'latest'))

  latest.reject(new Error('current failure'))
  await latestRun

  assert.deepEqual(events, ['latest:error', 'latest:settled'])
}

console.log('PASS: only the latest directory request mutates success, error, and settled state')
