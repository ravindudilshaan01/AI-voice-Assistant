const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

const source = fs.readFileSync(path.join(__dirname, '../src/services/speechGate.ts'), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
const moduleRef = { exports: {} }
new Function('module', 'exports', compiled)(moduleRef, moduleRef.exports)
const { SpeechGate } = moduleRef.exports

test('does not confirm silence or one noise spike', () => {
  const gate = new SpeechGate()
  for (let time = 0; time < 500; time += 20) gate.update(0.004, time)
  gate.update(0.2, 520)
  assert.equal(gate.hasSpeech, false)
})

test('confirms sustained speech and waits for silence to release', () => {
  const gate = new SpeechGate()
  for (let time = 0; time < 500; time += 20) gate.update(0.004, time)
  for (let time = 520; time <= 760; time += 20) gate.update(0.08, time)
  assert.equal(gate.hasSpeech, true)
  assert.equal(gate.update(0.004, 1700).speechActive, true)
  assert.equal(gate.update(0.004, 2300).speechActive, false)
})
