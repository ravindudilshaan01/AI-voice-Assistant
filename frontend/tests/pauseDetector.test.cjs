const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

const source = fs.readFileSync(path.join(__dirname, '../src/services/pauseDetector.ts'), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
const moduleRef = { exports: {} }
new Function('module', 'exports', compiled)(moduleRef, moduleRef.exports)
const { PauseDetector } = moduleRef.exports

test('ignores background noise and a short spike', () => {
  const detector = new PauseDetector()
  for (let time = 0; time < 600; time += 20) detector.update(0.004, time)
  detector.update(0.04, 620)
  assert.equal(detector.update(0.004, 900).speechConfirmed, false)
})

test('stops only after sustained speech followed by a long pause', () => {
  const detector = new PauseDetector()
  for (let time = 0; time < 500; time += 20) detector.update(0.004, time)
  for (let time = 500; time <= 900; time += 20) detector.update(0.04, time)
  assert.equal(detector.update(0.004, 2000).shouldStop, false)
  assert.equal(detector.update(0.004, 2820).shouldStop, true)
})
