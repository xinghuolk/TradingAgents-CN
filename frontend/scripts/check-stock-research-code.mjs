import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'

const require = createRequire(import.meta.url)
const source = readFileSync(new URL('../src/api/stockResearch.ts', import.meta.url), 'utf8')
const exports = {}
runInNewContext(
  ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
  }).outputText,
  {
    exports,
    require: name => (name === './request' ? { ApiClient: {} } : require(name))
  }
)

for (const code of ['0700', '00700', '0700.HK']) {
  assert.equal(exports.normalizeResearchCode('HK', code), '00700')
}
assert.equal(exports.normalizeResearchCode('CN', '600519'), '600519')
assert.equal(exports.normalizeResearchCode('US', 'aapl'), 'AAPL')

const detail = readFileSync(new URL('../src/views/Stocks/Detail.vue', import.meta.url), 'utf8')
assert.match(detail, /normalizeResearchCode\(currentMarket, code\.value\)/)

console.log('PASS stock detail research code normalization')
