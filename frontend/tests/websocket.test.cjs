const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

const source = fs.readFileSync(path.join(__dirname, '../src/services/websocket.ts'), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
const moduleRef = { exports: {} }
new Function('module', 'exports', compiled)(moduleRef, moduleRef.exports)
const { NovaWebSocket } = moduleRef.exports

class FakeWebSocket {
  static OPEN = 1
  static instances = []
  readyState = FakeWebSocket.OPEN
  sent = []
  constructor(url) { this.url = url; FakeWebSocket.instances.push(this) }
  send(value) { this.sent.push(value) }
  close() { this.readyState = 3; this.onclose?.() }
}

test('sends cancellation and accepts structured tool results', () => {
  global.WebSocket = FakeWebSocket
  const events = []
  const client = new NovaWebSocket('ws://test/ws', { onState: () => {}, onEvent: (event) => events.push(event), onAudio: () => {} })
  client.connect()
  const socket = FakeWebSocket.instances.at(-1)
  socket.onopen()
  assert.equal(client.sendMessage('Hello', 'r0', true, 'autumn'), true)
  assert.deepEqual(JSON.parse(socket.sent[0]), { type: 'message', text: 'Hello', requestId: 'r0', speak: true, voice: 'autumn' })
  assert.equal(client.stopGeneration(), true)
  assert.deepEqual(JSON.parse(socket.sent[1]), { type: 'stop_generation' })
  socket.onmessage({ data: JSON.stringify({ type: 'tool_result', requestId: 'r1', name: 'web_search', label: 'Searching', ok: true, result: { source: 'https://example.com' } }) })
  assert.equal(events[0].type, 'tool_result')
  assert.equal(events[0].result.source, 'https://example.com')
  socket.onmessage({ data: JSON.stringify({ type: 'reminder', reminderId: 'reminder-1', title: 'Check the oven', dueAt: '2026-11-07T20:00:00+05:30' }) })
  assert.equal(events[1].type, 'reminder')
  assert.equal(events[1].title, 'Check the oven')
  socket.onmessage({ data: JSON.stringify({ type: 'tts_error', responseId: 'r1', message: 'Natural voice unavailable.' }) })
  assert.equal(events[2].type, 'tts_error')
  client.disconnect()
})

test('manual retry creates a fresh connection', () => {
  global.WebSocket = FakeWebSocket
  const states = []
  const client = new NovaWebSocket('ws://test/ws', { onState: (state) => states.push(state), onEvent: () => {}, onAudio: () => {} })
  client.connect()
  const first = FakeWebSocket.instances.at(-1)
  first.onclose()
  client.retry()
  assert.notEqual(FakeWebSocket.instances.at(-1), first)
  assert.equal(states.at(-1), 'connecting')
  client.disconnect()
})
