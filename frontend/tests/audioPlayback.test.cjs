const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

const source = fs.readFileSync(path.join(__dirname, '../src/services/audioPlayback.ts'), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
const moduleRef = { exports: {} }
new Function('module', 'exports', compiled)(moduleRef, moduleRef.exports)
const { AudioPlayback } = moduleRef.exports

function fakeBrowser() {
  const players = []
  const spoken = []
  const revoked = []
  let cancelled = 0
  global.SpeechSynthesisUtterance = class { constructor(text) { this.text = text } }
  global.window = { SpeechSynthesisUtterance: global.SpeechSynthesisUtterance, speechSynthesis: {
    cancel: () => { cancelled++ },
    speak: (utterance) => spoken.push(utterance),
  } }
  global.URL.createObjectURL = () => `blob:segment-${players.length}`
  global.URL.revokeObjectURL = (url) => revoked.push(url)
  global.Audio = class {
    constructor(url) { this.url = url; this.paused = false; players.push(this) }
    play() { return Promise.resolve() }
    pause() { this.paused = true }
  }
  return { players, spoken, revoked, get cancelled() { return cancelled } }
}

test('plays out-of-order arrivals in segment order and cleans up', () => {
  const browser = fakeBrowser()
  const states = []
  const player = new AudioPlayback((state) => states.push(state), () => {})
  player.start('reply', true)
  const meta = (index) => ({ responseId: 'reply', segmentId: `reply:${index}`, index, mimeType: 'audio/wav', text: `Segment ${index}` })
  player.enqueueAudio(meta(1), new ArrayBuffer(2))
  assert.equal(browser.players.length, 0)
  player.enqueueAudio(meta(0), new ArrayBuffer(1))
  assert.equal(browser.players.length, 1)
  player.complete('reply', 2)
  browser.players[0].onended()
  assert.equal(browser.players.length, 2)
  browser.players[1].onended()
  assert.equal(states.at(-1), 'idle')
  assert.equal(browser.revoked.length, 2)
  player.dispose()
})

test('stop interrupts playback and ignores late segments', () => {
  const browser = fakeBrowser()
  const states = []
  const player = new AudioPlayback((state) => states.push(state), () => {})
  player.start('reply', true)
  const meta = (index) => ({ responseId: 'reply', segmentId: `reply:${index}`, index, mimeType: 'audio/wav', text: `Segment ${index}` })
  player.enqueueAudio(meta(0), new ArrayBuffer(1))
  player.stop()
  assert.equal(browser.players[0].paused, true)
  assert.equal(browser.revoked.length, 1)
  player.enqueueAudio(meta(1), new ArrayBuffer(1))
  assert.equal(browser.players.length, 1)
  assert.equal(states.at(-1), 'stopped')
})

test('browser speech fallback preserves text and advances queue', () => {
  const browser = fakeBrowser()
  const states = []
  const player = new AudioPlayback((state) => states.push(state), () => {})
  player.start('reply', true)
  player.enqueueFallback('reply', 0, 'Hello there.')
  player.complete('reply', 1)
  assert.equal(browser.spoken[0].text, 'Hello there.')
  browser.spoken[0].onend()
  assert.equal(states.at(-1), 'idle')
  player.dispose()
})
