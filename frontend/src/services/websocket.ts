export type ConnectionState = 'connecting' | 'connected' | 'disconnected'
export type AudioMetadata = { type: 'tts_audio'; responseId: string; segmentId: string; index: number; mimeType: 'audio/wav'; text: string }
export type ServerEvent =
  | { type: 'connection'; status: 'connected' }
  | { type: 'chat_started'; requestId: string; speak?: boolean }
  | { type: 'chat_delta'; requestId: string; text: string }
  | { type: 'chat_done'; requestId: string }
  | { type: 'chat_cancelled'; requestId: string }
  | { type: 'tool_activity'; requestId: string; name: string; label: string; state: 'started' | 'done' }
  | { type: 'tool_result'; requestId: string; name: string; label: string; ok: boolean; result?: Record<string, unknown>; error?: string }
  | { type: 'reminder'; reminderId: string; title: string; dueAt: string }
  | { type: 'tts_fallback'; responseId: string; segmentId: string; index: number; text: string; message: string }
  | { type: 'tts_error'; responseId: string; message: string }
  | { type: 'tts_complete'; responseId: string; segmentCount: number }
  | { type: 'tts_stopped' }
  | { type: 'tts_provider_changed'; responseId: string; from: 'kokoro' | 'groq'; to: 'kokoro' | 'groq' }
  | { type: 'listening'; requestId: string; state: 'LISTENING' }
  | { type: 'speech_started'; requestId: string }
  | { type: 'speech_ended'; requestId: string; frames: number; speechDetected: boolean; state: 'TRANSCRIBING' }
  | { type: 'transcript'; requestId: string; text: string }
  | { type: 'transcript_only'; requestId: string; text: string }
  | { type: 'cleared' }
  | { type: 'pong' }
  | { type: 'error'; message: string; requestId?: string }

type Listeners = {
  onState: (state: ConnectionState) => void
  onEvent: (event: ServerEvent) => void
  onAudio: (metadata: AudioMetadata, data: ArrayBuffer) => void
}

export class NovaWebSocket {
  private socket: WebSocket | null = null
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private attempts = 0
  private stopped = false
  private state: ConnectionState = 'disconnected'
  private pendingAudio: AudioMetadata | null = null

  constructor(private readonly url: string, private readonly listeners: Listeners) {}

  connect(): void {
    if (this.stopped || this.socket) return
    this.setState('connecting')
    const socket = new WebSocket(this.url)
    socket.binaryType = 'arraybuffer'
    this.socket = socket

    socket.onopen = () => {
      if (this.socket !== socket) return
      this.attempts = 0
      this.setState('connected')
    }
    socket.onmessage = (message) => {
      try {
        if (message.data instanceof ArrayBuffer) {
          if (this.pendingAudio) this.listeners.onAudio(this.pendingAudio, message.data)
          this.pendingAudio = null
          return
        }
        const event: unknown = JSON.parse(message.data)
        if (isAudioMetadata(event)) {
          this.pendingAudio = event
          return
        }
        if (isServerEvent(event)) this.listeners.onEvent(event)
      } catch {
        this.listeners.onEvent({ type: 'error', message: 'Invalid server response' })
      }
    }
    socket.onclose = () => {
      if (this.socket !== socket) return
      this.socket = null
      this.pendingAudio = null
      this.setState('disconnected')
      if (!this.stopped) {
        const delay = Math.min(1000 * 2 ** this.attempts++, 10000)
        this.retryTimer = setTimeout(() => {
          this.retryTimer = null
          this.connect()
        }, delay)
      }
    }
    socket.onerror = () => socket.close()
  }

  sendMessage(text: string, requestId: string, speak = true, voice = 'male', provider = 'kokoro'): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN) return false
    this.socket.send(JSON.stringify({ type: 'message', text, requestId, speak, voice, provider }))
    return true
  }

  startPcmStream(requestId: string, sampleRate = 16000): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN) return false
    this.socket.send(JSON.stringify({ type: 'pcm_stream_start', requestId, sampleRate, format: 's16le' }))
    return true
  }

  sendPcmFrame(sequence: number, timestampMs: number, sampleRate: number, pcm: Int16Array): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN || this.socket.bufferedAmount > 512 * 1024) return false
    const packet = new ArrayBuffer(16 + pcm.byteLength)
    const view = new DataView(packet)
    view.setUint32(0, sequence, true)
    view.setFloat64(4, timestampMs, true)
    view.setUint32(12, sampleRate, true)
    new Int16Array(packet, 16).set(pcm)
    this.socket.send(packet)
    return true
  }

  endPcmStream(requestId: string): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN) return false
    this.socket.send(JSON.stringify({ type: 'pcm_stream_end', requestId }))
    return true
  }

  sendMetric(name: string, durationMs: number): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN || !Number.isFinite(durationMs)) return false
    this.socket.send(JSON.stringify({ type: 'client_metric', name, durationMs }))
    return true
  }

  async sendAudio(blob: Blob, mimeType: string, requestId: string, speak = true, transcribeOnly = false, voice = 'male', provider = 'kokoro'): Promise<boolean> {
    const data = await blob.arrayBuffer()
    if (this.socket?.readyState !== WebSocket.OPEN) return false
    this.socket.send(JSON.stringify({ type: 'audio_start', mimeType, size: data.byteLength, requestId, speak, transcribeOnly, voice, provider }))
    this.socket.send(data)
    return true
  }

  clear(): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN) return false
    this.socket.send(JSON.stringify({ type: 'clear' }))
    return true
  }

  stopSpeaking(): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN) return false
    this.socket.send(JSON.stringify({ type: 'stop_speaking' }))
    return true
  }

  stopGeneration(): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN) return false
    this.socket.send(JSON.stringify({ type: 'stop_generation' }))
    return true
  }

  speakText(text: string, requestId: string, voice = 'male', provider = 'kokoro'): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN) return false
    this.socket.send(JSON.stringify({ type: 'speak_text', text, requestId, voice, provider }))
    return true
  }

  retry(): void {
    if (this.state === 'connected') return
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.retryTimer = null
    if (this.socket) {
      const socket = this.socket
      this.socket = null
      socket.close()
    }
    this.connect()
  }

  disconnect(): void {
    this.stopped = true
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.retryTimer = null
    this.socket?.close()
    this.socket = null
    this.pendingAudio = null
    this.setState('disconnected')
  }

  private setState(state: ConnectionState): void {
    this.state = state
    this.listeners.onState(this.state)
  }
}

function isServerEvent(value: unknown): value is ServerEvent {
  if (typeof value !== 'object' || value === null || !('type' in value)) return false
  const event = value as Record<string, unknown>
  return (event.type === 'connection' && event.status === 'connected') ||
    (event.type === 'chat_started' && typeof event.requestId === 'string') ||
    (event.type === 'chat_delta' && typeof event.requestId === 'string' && typeof event.text === 'string') ||
    (event.type === 'chat_done' && typeof event.requestId === 'string') ||
    (event.type === 'chat_cancelled' && typeof event.requestId === 'string') ||
    (event.type === 'tool_activity' && typeof event.requestId === 'string' && typeof event.name === 'string' && typeof event.label === 'string' && (event.state === 'started' || event.state === 'done')) ||
    (event.type === 'tool_result' && typeof event.requestId === 'string' && typeof event.name === 'string' && typeof event.label === 'string' && typeof event.ok === 'boolean' &&
      (event.result === undefined || (typeof event.result === 'object' && event.result !== null)) &&
      (event.error === undefined || typeof event.error === 'string')) ||
    (event.type === 'reminder' && typeof event.reminderId === 'string' && typeof event.title === 'string' && typeof event.dueAt === 'string') ||
    (event.type === 'tts_fallback' && typeof event.responseId === 'string' && typeof event.segmentId === 'string' && Number.isInteger(event.index) && typeof event.text === 'string') ||
    (event.type === 'tts_error' && typeof event.responseId === 'string' && typeof event.message === 'string') ||
    (event.type === 'tts_complete' && typeof event.responseId === 'string' && Number.isInteger(event.segmentCount)) ||
    event.type === 'tts_stopped' ||
    (event.type === 'tts_provider_changed' && typeof event.responseId === 'string' &&
      (event.from === 'kokoro' || event.from === 'groq') && (event.to === 'kokoro' || event.to === 'groq')) ||
    (event.type === 'listening' && typeof event.requestId === 'string') ||
    (event.type === 'speech_started' && typeof event.requestId === 'string') ||
    (event.type === 'speech_ended' && typeof event.requestId === 'string' && typeof event.frames === 'number' && typeof event.speechDetected === 'boolean') ||
    (event.type === 'transcript' && typeof event.requestId === 'string' && typeof event.text === 'string') ||
    (event.type === 'transcript_only' && typeof event.requestId === 'string' && typeof event.text === 'string') ||
    event.type === 'cleared' ||
    event.type === 'pong' ||
    (event.type === 'error' && typeof event.message === 'string' &&
      (event.requestId === undefined || typeof event.requestId === 'string'))
}

function isAudioMetadata(value: unknown): value is AudioMetadata {
  if (typeof value !== 'object' || value === null) return false
  const event = value as Record<string, unknown>
  return event.type === 'tts_audio' && typeof event.responseId === 'string' &&
    typeof event.segmentId === 'string' && Number.isInteger(event.index) &&
    event.mimeType === 'audio/wav' && typeof event.text === 'string'
}
