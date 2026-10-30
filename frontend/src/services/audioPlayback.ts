import type { AudioMetadata } from './websocket'

type Segment = { kind: 'audio'; text: string; data: ArrayBuffer } | { kind: 'fallback'; text: string }
export type PlaybackStatus = 'idle' | 'preparing' | 'speaking' | 'browser' | 'stopped' | 'muted'

export class AudioPlayback {
  private responseId: string | null = null
  private segments = new Map<number, Segment>()
  private nextIndex = 0
  private completedCount: number | null = null
  private playing = false
  private suppressed = false
  private muted = false
  private audio: HTMLAudioElement | null = null
  private url: string | null = null
  private generation = 0

  constructor(
    private readonly onStatus: (status: PlaybackStatus) => void,
    private readonly onError: (message: string) => void,
  ) {}

  start(responseId: string, speak: boolean): void {
    this.cancelCurrent()
    this.responseId = responseId
    this.suppressed = !speak || this.muted
    this.onStatus(this.muted ? 'muted' : speak ? 'preparing' : 'idle')
  }

  enqueueAudio(metadata: AudioMetadata, data: ArrayBuffer): void {
    if (!this.accepts(metadata.responseId, metadata.index)) return
    this.segments.set(metadata.index, { kind: 'audio', text: metadata.text, data })
    this.pump()
  }

  enqueueFallback(responseId: string, index: number, text: string): void {
    if (!this.accepts(responseId, index)) return
    this.segments.set(index, { kind: 'fallback', text })
    this.pump()
  }

  complete(responseId: string, count: number): void {
    if (this.responseId !== responseId || this.suppressed) return
    this.completedCount = count
    this.pump()
  }

  stop(): void {
    this.suppressed = true
    this.cancelCurrent()
    this.onStatus(this.muted ? 'muted' : 'stopped')
  }

  setMuted(muted: boolean): void {
    this.muted = muted
    if (muted) this.stop()
    else this.onStatus('idle')
  }

  dispose(): void {
    this.stop()
    this.responseId = null
  }

  private accepts(responseId: string, index: number): boolean {
    return this.responseId === responseId && !this.suppressed && !this.muted &&
      Number.isInteger(index) && index >= this.nextIndex
  }

  private cancelCurrent(): void {
    this.generation++
    this.audio?.pause()
    if (this.audio) {
      this.audio.onended = null
      this.audio.onerror = null
    }
    this.audio = null
    if (this.url) URL.revokeObjectURL(this.url)
    this.url = null
    if ('speechSynthesis' in window) window.speechSynthesis.cancel()
    this.segments.clear()
    this.nextIndex = 0
    this.completedCount = null
    this.playing = false
  }

  private finishSegment(token: number): void {
    if (token !== this.generation) return
    this.audio = null
    if (this.url) URL.revokeObjectURL(this.url)
    this.url = null
    this.playing = false
    this.nextIndex++
    this.pump()
  }

  private pump(): void {
    if (this.playing || this.suppressed || this.muted) return
    const segment = this.segments.get(this.nextIndex)
    if (!segment) {
      if (this.completedCount !== null && this.nextIndex >= this.completedCount) this.onStatus('idle')
      return
    }
    this.segments.delete(this.nextIndex)
    this.playing = true
    this.onStatus('speaking')
    const token = this.generation
    if (segment.kind === 'fallback') {
      this.speakInBrowser(segment.text, token)
      return
    }
    this.url = URL.createObjectURL(new Blob([segment.data], { type: 'audio/wav' }))
    const audio = new Audio(this.url)
    this.audio = audio
    let handled = false
    const useFallback = () => {
      if (handled || token !== this.generation) return
      handled = true
      audio.pause()
      audio.onended = null
      audio.onerror = null
      this.audio = null
      if (this.url) URL.revokeObjectURL(this.url)
      this.url = null
      this.speakInBrowser(segment.text, token)
    }
    audio.onended = () => { if (!handled) { handled = true; this.finishSegment(token) } }
    audio.onerror = useFallback
    void audio.play().catch(useFallback)
  }

  private speakInBrowser(text: string, token: number): void {
    if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
      this.onError('Audio playback is unavailable in this browser.')
      this.finishSegment(token)
      return
    }
    const utterance = new SpeechSynthesisUtterance(text)
    this.onStatus('browser')
    utterance.onend = () => this.finishSegment(token)
    utterance.onerror = () => {
      if (token === this.generation) this.onError('Browser speech playback failed. Try again or unmute your device.')
      this.finishSegment(token)
    }
    try {
      window.speechSynthesis.speak(utterance)
    } catch {
      this.onError('Browser speech playback failed. Try again or unmute your device.')
      this.finishSegment(token)
    }
  }
}
