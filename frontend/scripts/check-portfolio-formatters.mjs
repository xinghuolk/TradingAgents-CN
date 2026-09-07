import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

const source = readFileSync(
  new URL('../src/views/Portfolio/portfolioFormatters.ts', import.meta.url),
  'utf8'
)
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
}).outputText
const exports = {}
runInNewContext(code, { exports, require: createRequire(import.meta.url) })

for (const [value, expected] of [
  ['2026-09-07', '2026-09-07'],
  ['2026-09-07T08:01:02Z', '2026-09-07'],
  ['2026-09-07T08:01:02.123456+08:00', '2026-09-07'],
  ['2026-09-07T08:01:02', '2026-09-07'],
  ['2026-09-07T', '-'],
  ['2026-09-07Tgarbage', '-'],
  ['2026-09-07T25:00:00Z', '-'],
  ['2026-09-07T08:60:00Z', '-'],
  ['2026-09-07T08:01:60Z', '-'],
  ['2026-09-07T08:01:02+25:00', '-'],
  ['2026-02-30T08:01:02Z', '-'],
  ['2026-02-30', '-'],
  ['', '-'],
  [null, '-']
]) {
  assert.equal(exports.formatDate(value), expected, String(value))
}
assert.equal(
  exports.formatDecimal('123456789012345678901234567890.123456789'),
  '123,456,789,012,345,678,901,234,567,890.123456789'
)
console.log('PASS: complete portfolio dates, timestamps and exact decimal display')
