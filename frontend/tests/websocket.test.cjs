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
  assert.deepEqual(JSON.parse(socket.sent[0]), { type: 'message', text: 'Hello', requestId: 'r0', speak: true, voice: 'autumn', provider: 'kokoro' })
  assert.equal(client.stopGeneration(), true)
  assert.deepEqual(JSON.parse(socket.sent[1]), { type: 'stop_generation' })
  assert.equal(client.speakText('Natural replay.', 'replay-1', 'autumn'), true)
  assert.deepEqual(JSON.parse(socket.sent[2]), { type: 'speak_text', text: 'Natural replay.', requestId: 'replay-1', voice: 'autumn', provider: 'kokoro' })
  assert.equal(client.startPcmStream('pcm-1'), true)
  assert.deepEqual(JSON.parse(socket.sent[3]), { type: 'pcm_stream_start', requestId: 'pcm-1', sampleRate: 16000, format: 's16le' })
  const pcm = new Int16Array([1, -2, 3])
  assert.equal(client.sendPcmFrame(7, 123.5, 16000, pcm), true)
  const frame = socket.sent[4]
  const view = new DataView(frame)
  assert.equal(view.getUint32(0, true), 7)
  assert.equal(view.getFloat64(4, true), 123.5)
  assert.equal(view.getUint32(12, true), 16000)
  assert.deepEqual([...new Int16Array(frame, 16)], [1, -2, 3])
  assert.equal(client.endPcmStream('pcm-1'), true)
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
