import { lazy, Suspense, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ToolCard, type ToolView } from './components/ToolCard'
import { AudioPlayback, type PlaybackStatus } from './services/audioPlayback'
import { PauseDetector } from './services/pauseDetector'
import { NovaWebSocket, type ConnectionState, type ServerEvent } from './services/websocket'

type VoiceMode = 'cloud' | 'browser' | 'text'
type VoicePersona = 'male' | 'female'
type PermissionState = 'prompt' | 'granted' | 'denied'
type Entry = {
  id: string
  requestId: string
  role: 'you' | 'nova'
  text: string
  complete?: boolean
  cancelled?: boolean
  tools?: ToolView[]
}

const wsUrl = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws'
const maxAudioBytes = 10 * 1024 * 1024
const maxRecordingMs = 60_000
const audioTypes = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4', 'audio/webm', 'audio/ogg']
const quickPrompts = ['What can you do?', 'Weather in Colombo', 'Set a reminder']
const MarkdownMessage = lazy(() => import('./components/MarkdownMessage').then((module) => ({ default: module.MarkdownMessage })))

const hasSeenMicIntro = () => {
  try { return localStorage.getItem('nova-mic-intro') === 'seen' } catch { return false }
}

const savedVoicePersona = (): VoicePersona => {
  try { return localStorage.getItem('nova-voice-persona') === 'female' ? 'female' : 'male' } catch { return 'male' }
}

const Icon = ({ name }: { name: 'settings' | 'copy' | 'retry' | 'speak' | 'delete' | 'close' }) => {
  const paths = {
    settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></>,
    copy: <><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></>,
    retry: <><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></>,
    speak: <><path d="M11 5 6 9H3v6h3l5 4Z"/><path d="M15 9a4 4 0 0 1 0 6m3-9a8 8 0 0 1 0 12"/></>,
    delete: <><path d="M4 7h16M9 7V4h6v3m3 0-1 14H7L6 7m4 4v6m4-6v6"/></>,
    close: <path d="m5 5 14 14M19 5 5 19"/>,
  }
  return <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>
}

export default function App() {
  const [status, setStatus] = useState<ConnectionState>('connecting')
  const [entries, setEntries] = useState<Entry[]>([])
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [toolActivity, setToolActivity] = useState('')
  const [playbackStatus, setPlaybackStatus] = useState<PlaybackStatus>('idle')
  const [voiceMode, setVoiceMode] = useState<VoiceMode>('cloud')
  const [voicePersona, setVoicePersona] = useState<VoicePersona>(savedVoicePersona)
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [audioInputs, setAudioInputs] = useState<MediaDeviceInfo[]>([])
  const [selectedAudioInput, setSelectedAudioInput] = useState('')
  const [seconds, setSeconds] = useState(0)
  const [level, setLevel] = useState(0)
  const [activeInput, setActiveInput] = useState('')
  const [inputSampleRate, setInputSampleRate] = useState<number | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [permission, setPermission] = useState<PermissionState>('prompt')
  const [showMicIntro, setShowMicIntro] = useState(() => !hasSeenMicIntro())
  const [announcement, setAnnouncement] = useState('Assistant is ready')
  const [debugOpen, setDebugOpen] = useState(false)
  const [debugRecording, setDebugRecording] = useState(false)
  const [debugUrl, setDebugUrl] = useState('')
  const [debugTranscript, setDebugTranscript] = useState('')
  const [debugStats, setDebugStats] = useState('')

  const autoSelectAudioInput = useRef(true)
  const client = useRef<NovaWebSocket | null>(null)
  const playback = useRef<AudioPlayback | null>(null)
  const voiceModeRef = useRef<VoiceMode>('cloud')
  const voicePersonaRef = useRef<VoicePersona>(voicePersona)
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const audioContext = useRef<AudioContext | null>(null)
  const animation = useRef<number | null>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)
  const end = useRef<HTMLDivElement | null>(null)
  const transcript = useRef<HTMLDivElement | null>(null)
  const entriesRef = useRef<Entry[]>([])
  const responseText = useRef(new Map<string, string>())
  const busyRef = useRef(false)
  const recordingRef = useRef(false)
  const startedAt = useRef(0)
  const pauseDetector = useRef<PauseDetector | null>(null)
  const startingRecording = useRef(false)

  useEffect(() => { entriesRef.current = entries }, [entries])
  useEffect(() => { voiceModeRef.current = voiceMode }, [voiceMode])
  useEffect(() => { voicePersonaRef.current = voicePersona }, [voicePersona])

  const selectedCloudVoice = () => voicePersonaRef.current === 'female' ? 'autumn' : 'troy'
  const selectVoicePersona = (value: VoicePersona) => {
    voicePersonaRef.current = value
    setVoicePersona(value)
    playback.current?.setVoicePersona(value)
    try { localStorage.setItem('nova-voice-persona', value) } catch { /* Optional preference storage. */ }
  }

  const setProcessing = (value: boolean) => { busyRef.current = value; setBusy(value) }
  const releaseMicrophone = () => {
    stream.current?.getTracks().forEach((track) => track.stop())
    stream.current = null
    if (animation.current !== null) cancelAnimationFrame(animation.current)
    animation.current = null
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    void audioContext.current?.close()
    audioContext.current = null
    setLevel(0)
    pauseDetector.current = null
    startingRecording.current = false
    recordingRef.current = false
    setRecording(false)
  }

  const refreshAudioInputs = async (preferredDeviceId?: string) => {
    if (!navigator.mediaDevices?.enumerateDevices) return
    try {
      const devices = (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === 'audioinput')
      setAudioInputs(devices)
      setSelectedAudioInput((current) => {
        if (preferredDeviceId && devices.some((device) => device.deviceId === preferredDeviceId)) return preferredDeviceId
        if (!autoSelectAudioInput.current && current && devices.some((device) => device.deviceId === current)) return current
        const systemDefault = devices.find((device) => device.deviceId === 'default')
        return current && devices.some((device) => device.deviceId === current) ? current : systemDefault?.deviceId || devices[0]?.deviceId || ''
      })
    } catch {
      setAudioInputs([])
    }
  }

  useEffect(() => {
    void refreshAudioInputs()
    if ('permissions' in navigator) {
      void navigator.permissions.query({ name: 'microphone' as PermissionName }).then((result) => {
        const update = () => setPermission(result.state as PermissionState)
        update()
        result.addEventListener('change', update)
      }).catch(() => undefined)
    }
    const handleDeviceChange = () => {
      if (autoSelectAudioInput.current) releaseMicrophone()
      void refreshAudioInputs()
    }
    navigator.mediaDevices?.addEventListener('devicechange', handleDeviceChange)
    return () => navigator.mediaDevices?.removeEventListener('devicechange', handleDeviceChange)
  }, [])

  useEffect(() => {
    const updatePlaybackStatus = (next: PlaybackStatus) => {
      setPlaybackStatus(next)
    }
    const player = new AudioPlayback(updatePlaybackStatus, setError)
    player.setVoicePersona(voicePersonaRef.current)
    playback.current = player

    const updateNova = (requestId: string, updater: (entry: Entry) => Entry) => {
      setEntries((current) => current.map((entry) => entry.id === `nova-${requestId}` ? updater(entry) : entry))
    }
    const handleEvent = (event: ServerEvent) => {
      if (event.type === 'transcript') {
        setTranscribing(false)
        setEntries((current) => [...current, { id: `user-${event.requestId}`, requestId: event.requestId, role: 'you', text: event.text }])
      } else if (event.type === 'transcript_only') {
        setDebugTranscript(event.text)
      } else if (event.type === 'chat_started') {
        setTranscribing(false)
        responseText.current.set(event.requestId, '')
        player.start(event.requestId, event.speak !== false)
        setEntries((current) => [...current, { id: `nova-${event.requestId}`, requestId: event.requestId, role: 'nova', text: '', tools: [] }])
      } else if (event.type === 'chat_delta') {
        responseText.current.set(event.requestId, (responseText.current.get(event.requestId) || '') + event.text)
        updateNova(event.requestId, (entry) => ({ ...entry, text: entry.text + event.text }))
      } else if (event.type === 'chat_done') {
        setProcessing(false)
        setToolActivity('')
        updateNova(event.requestId, (entry) => ({ ...entry, complete: true }))
        if (voiceModeRef.current === 'browser') player.speakText(responseText.current.get(event.requestId) || '')
        setAnnouncement('Assistant finished responding')
      } else if (event.type === 'chat_cancelled') {
        setProcessing(false)
        setToolActivity('')
        player.stop()
        updateNova(event.requestId, (entry) => ({ ...entry, complete: true, cancelled: true }))
        setAnnouncement('Response stopped')
      } else if (event.type === 'tool_activity') {
        setToolActivity(event.state === 'started' ? event.label : '')
        updateNova(event.requestId, (entry) => {
          const tools = [...(entry.tools || [])]
          const index = tools.findIndex((tool) => tool.name === event.name && tool.state === 'running')
          if (event.state === 'started' && index < 0) tools.push({ id: `${event.requestId}-${event.name}-${tools.length}`, name: event.name, label: event.label, state: 'running' })
          return { ...entry, tools }
        })
      } else if (event.type === 'tool_result') {
        updateNova(event.requestId, (entry) => {
          const tools = [...(entry.tools || [])]
          const index = tools.findIndex((tool) => tool.name === event.name && tool.state === 'running')
          const result: ToolView = { id: index >= 0 ? tools[index].id : `${event.requestId}-${event.name}-${tools.length}`, name: event.name, label: event.label, state: event.ok ? 'done' : 'error', result: event.result, error: event.error }
          if (index >= 0) tools[index] = result
          else tools.push(result)
          return { ...entry, tools }
        })
      } else if (event.type === 'reminder') {
        player.start(event.reminderId, true)
        setEntries((current) => [...current, {
          id: event.reminderId,
          requestId: event.reminderId,
          role: 'nova',
          text: `Reminder: ${event.title}`,
          complete: true,
        }])
        setAnnouncement(`Reminder: ${event.title}`)
      } else if (event.type === 'tts_fallback') {
        player.enqueueFallback(event.responseId, event.index, event.text)
      } else if (event.type === 'tts_error') {
        player.stop()
        setError(event.message)
        setAnnouncement('Natural voice unavailable')
      } else if (event.type === 'tts_complete') {
        player.complete(event.responseId, event.segmentCount)
      } else if (event.type === 'tts_stopped') {
        player.stop()
      } else if (event.type === 'error') {
        setTranscribing(false)
        player.stop()
        setError(event.message)
        setProcessing(false)
        setToolActivity('')
        if (event.requestId) setEntries((current) => current.filter((entry) => entry.id !== `nova-${event.requestId}` || entry.text.length > 0))
      }
    }
    const service = new NovaWebSocket(wsUrl, {
      onState: (state) => {
        setStatus(state)
        if (state === 'disconnected') {
          setTranscribing(false)
          setProcessing(false)
          setToolActivity('')
          player.stop()
        }
      },
      onEvent: handleEvent,
      onAudio: (metadata, data) => player.enqueueAudio(metadata, data),
    })
    client.current = service
    service.connect()
    return () => {
      if (recorder.current) {
        recorder.current.onstop = null
        recorder.current.onerror = null
        if (recorder.current.state === 'recording') recorder.current.stop()
        recorder.current = null
      }
      releaseMicrophone()
      player.dispose()
      playback.current = null
      service.disconnect()
      client.current = null
    }
  }, [])

  useEffect(() => {
    if (entries.length === 0 || !transcript.current) return
    transcript.current.scrollTo({ top: transcript.current.scrollHeight, behavior: 'smooth' })
  }, [entries])
  useEffect(() => {
    if (!settingsOpen) return
    const closeOnEscape = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') setSettingsOpen(false) }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [settingsOpen])

  const submitText = (rawText: string) => {
    const text = rawText.trim()
    if (!text || busyRef.current || recordingRef.current) return
    playback.current?.stop()
    client.current?.stopSpeaking()
    const requestId = crypto.randomUUID()
    const speak = voiceModeRef.current === 'cloud'
    if (!client.current?.sendMessage(text, requestId, speak, selectedCloudVoice())) {
      setError('Connection unavailable. Your message was not sent.')
      return
    }
    setProcessing(true)
    setEntries((current) => [...current, { id: `user-${requestId}`, requestId, role: 'you', text }])
    setDraft('')
    setError('')
  }

  const send = (event: FormEvent) => { event.preventDefault(); submitText(draft) }
  const stopGeneration = () => {
    playback.current?.stop()
    if (!client.current?.stopGeneration()) setProcessing(false)
  }

  const startRecording = async () => {
    if (busyRef.current || recordingRef.current || startingRecording.current || status !== 'connected') return
    playback.current?.stop()
    client.current?.stopSpeaking()
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setError('Microphone recording is unavailable in this browser or context. Use localhost or HTTPS.')
      return
    }
    const mimeType = audioTypes.find((type) => MediaRecorder.isTypeSupported(type))
    if (!mimeType) {
      setError('This browser cannot record a supported format. Try Chrome, Edge, or Firefox.')
      return
    }
    startingRecording.current = true
    try {
      let media = stream.current
      const reusableTrack = media?.getAudioTracks()[0]
      if (!media || !reusableTrack || reusableTrack.readyState !== 'live') {
        media = await navigator.mediaDevices.getUserMedia({
          audio: {
            deviceId: selectedAudioInput ? { exact: selectedAudioInput } : undefined,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            channelCount: { ideal: 1 },
            sampleRate: { ideal: 48_000 },
            sampleSize: { ideal: 16 },
          },
        })
        setPermission('granted')
        setShowMicIntro(false)
        try { localStorage.setItem('nova-mic-intro', 'seen') } catch { /* Storage can be unavailable in private contexts. */ }
        stream.current = media
        const track = media.getAudioTracks()[0]
        const settings = track?.getSettings()
        const activeDeviceId = settings?.deviceId
        const devices = await navigator.mediaDevices.enumerateDevices()
        const device = devices.find((item) => item.deviceId === activeDeviceId)
        setActiveInput(device?.label || track?.label || activeDeviceId || 'Default microphone')
        setInputSampleRate(settings?.sampleRate || null)
        void refreshAudioInputs(activeDeviceId)
      }
      const mediaRecorder = new MediaRecorder(media, { mimeType })
      recorder.current = mediaRecorder
      const chunks: Blob[] = []
      let totalBytes = 0
      let rmsTotal = 0
      let rmsSamples = 0
      let peakLevel = 0
      let failed = false
      const requestId = crypto.randomUUID()
      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size === 0) return
        totalBytes += event.data.size
        chunks.push(event.data)
        if (totalBytes > maxAudioBytes && mediaRecorder.state === 'recording') mediaRecorder.stop()
      }
      mediaRecorder.onerror = () => { failed = true; setError('Recording failed. Please try again.'); releaseMicrophone() }
      mediaRecorder.onstop = async () => {
        if (timer.current) clearTimeout(timer.current)
        timer.current = null
        recordingRef.current = false
        setRecording(false)
        if (animation.current !== null) cancelAnimationFrame(animation.current)
        animation.current = null
        void audioContext.current?.close()
        audioContext.current = null
        recorder.current = null
        if (failed) return
        if (totalBytes === 0) { setError('The recording was empty. Please try again.'); return }
        if (totalBytes > maxAudioBytes) { setError('Recording is too large. Please record a shorter message.'); return }
        const averageRms = rmsSamples ? rmsTotal / rmsSamples : 0
        const demonstrablySilent = peakLevel < 0.006 && averageRms < 0.002
        if (!pauseDetector.current?.hasSpeech || demonstrablySilent) {
          setError('No clear speech detected. Please try again.')
          return
        }
        const actualType = mediaRecorder.mimeType || chunks[0].type
        const blob = new Blob(chunks, { type: actualType })
        setTranscribing(true)
        setProcessing(true)
        setError('')
        try {
          if (!await client.current?.sendAudio(blob, actualType, requestId, voiceModeRef.current === 'cloud', false, selectedCloudVoice())) {
            setTranscribing(false)
            setProcessing(false)
            setError('Connection unavailable. Recording was not sent.')
          }
        } catch {
          setTranscribing(false)
          setProcessing(false)
          setError('Could not send the recording. Please try again.')
        }
      }
      mediaRecorder.start(250)
      setSeconds(0)
      recordingRef.current = true
      pauseDetector.current = new PauseDetector()
      startedAt.current = performance.now()
      setRecording(true)
      startingRecording.current = false
      const updateRecordingTimer = () => {
        if (!recordingRef.current) return
        const elapsed = performance.now() - startedAt.current
        setSeconds(Math.floor(elapsed / 1000))
        timer.current = setTimeout(updateRecordingTimer, Math.max(50, 1000 - (elapsed % 1000)))
      }
      timer.current = setTimeout(updateRecordingTimer, 1000)

      const context = new AudioContext()
      audioContext.current = context
      await context.resume()
      const source = context.createMediaStreamSource(media)
      const analyser = context.createAnalyser()
      analyser.fftSize = 512
      analyser.smoothingTimeConstant = 0.72
      source.connect(analyser)
      const data = new Uint8Array(analyser.fftSize)
      const updateLevel = () => {
        if (!recordingRef.current) return
        const now = performance.now()
        analyser.getByteTimeDomainData(data)
        let sumSquares = 0
        for (const sample of data) {
          const centered = (sample - 128) / 128
          sumSquares += centered * centered
        }
        const rawRms = Math.sqrt(sumSquares / data.length)
        const nextLevel = Math.min(1, rawRms * 8)
        rmsTotal += rawRms
        rmsSamples += 1
        peakLevel = Math.max(peakLevel, rawRms)
        setLevel(nextLevel)
        if (pauseDetector.current?.update(rawRms, now).shouldStop && recorder.current?.state === 'recording') recorder.current.stop()
        if (recordingRef.current && now - startedAt.current >= maxRecordingMs) recorder.current?.stop()
        animation.current = requestAnimationFrame(updateLevel)
      }
      updateLevel()
    } catch (cause) {
      startingRecording.current = false
      releaseMicrophone()
      if (cause instanceof DOMException && (cause.name === 'NotAllowedError' || cause.name === 'PermissionDeniedError')) {
        setPermission('denied')
        setError('Microphone permission was denied. Allow access in your browser settings and try again.')
      } else {
        setError('Could not start the microphone. Check your device and try again.')
      }
    }
  }

  const stopRecording = () => { if (recorder.current?.state === 'recording') recorder.current.stop() }
  const clear = () => {
    if (busyRef.current || recordingRef.current) return
    if (!client.current?.clear()) { setError('Reconnect before clearing the conversation.'); return }
    playback.current?.stop()
    setEntries([])
    responseText.current.clear()
    setError('')
  }
  const retryEntry = (entry: Entry) => {
    const prompt = entriesRef.current.find((candidate) => candidate.role === 'you' && candidate.requestId === entry.requestId)?.text
    if (prompt) submitText(prompt)
  }
  const copyEntry = async (entry: Entry) => {
    try {
      await navigator.clipboard.writeText(entry.text)
      setAnnouncement('Response copied')
    } catch {
      setError('Could not copy this response.')
    }
  }
  const deleteEntry = (entry: Entry) => setEntries((current) => current.filter((candidate) => candidate.id !== entry.id))
  const speakEntry = (entry: Entry) => playback.current?.speakText(entry.text)

  const recordDebugSample = async () => {
    if (debugRecording || recordingRef.current || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') return
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: selectedAudioInput ? { exact: selectedAudioInput } : undefined, echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
      const mimeType = audioTypes.find((type) => MediaRecorder.isTypeSupported(type)) || 'audio/webm'
      const debugRecorder = new MediaRecorder(media, { mimeType })
      const chunks: Blob[] = []
      const started = performance.now()
      setDebugRecording(true)
      setDebugTranscript('')
      debugRecorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data) }
      debugRecorder.onstop = async () => {
        media.getTracks().forEach((track) => track.stop())
        const blob = new Blob(chunks, { type: debugRecorder.mimeType || mimeType })
        const url = URL.createObjectURL(blob)
        setDebugUrl((previous) => { if (previous) URL.revokeObjectURL(previous); return url })
        setDebugStats(`${Math.round(performance.now() - started)} ms · ${blob.size} bytes · ${blob.type}`)
        setDebugRecording(false)
        await client.current?.sendAudio(blob, blob.type, `debug-${crypto.randomUUID()}`, false, true)
      }
      debugRecorder.start(250)
      window.setTimeout(() => { if (debugRecorder.state === 'recording') debugRecorder.stop() }, 5000)
    } catch {
      setDebugRecording(false)
      setError('Could not start the debug microphone sample.')
    }
  }

  const assistantState = useMemo(() => {
    if (status !== 'connected') return status === 'connecting' ? 'Connecting' : 'Offline'
    if (permission === 'denied') return 'Permission needed'
    if (recording) return 'Listening'
    if (transcribing) return 'Transcribing'
    if (busy) return toolActivity || 'Thinking'
    if (['preparing', 'speaking', 'browser'].includes(playbackStatus)) return 'Speaking'
    return 'Ready'
  }, [status, permission, recording, transcribing, busy, toolActivity, playbackStatus])

  useEffect(() => { setAnnouncement(assistantState) }, [assistantState])

  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      if (!busy) submitText(draft)
    }
  }

  return <div className="app-shell">
    <div className="sr-only" aria-live="polite" aria-atomic="true">{announcement}</div>
    <header className="topbar">
      <div className="brand" aria-label="Voice workspace">
        <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
        <span className="brand-copy"><strong>Voice workspace</strong><small>Local session</small></span>
      </div>
      <div className="topbar-actions">
        <div className={`connection-pill ${status}`}><span className="status-dot" />{status}</div>
        <button className="icon-button settings-button" type="button" onClick={() => setSettingsOpen(true)} aria-label="Open settings"><Icon name="settings" /></button>
      </div>
    </header>

    {status === 'disconnected' && <div className="reconnect-banner" role="status">
      <span><strong>The assistant is offline.</strong> Your messages will stay here while we reconnect.</span>
      <button type="button" onClick={() => client.current?.retry()}>Retry connection</button>
    </div>}

    <main className="workspace">
      <section className={`voice-panel ${recording ? 'is-recording' : ''}`} aria-label="Voice controls">
        <div className="voice-panel-header">
          <span className="section-label">Voice input</span>
          <div className={`assistant-state state-${assistantState.toLowerCase().replace(/\s+/g, '-')}`}><span />{assistantState}</div>
        </div>

        <div className="level-meter" aria-hidden="true">
          {[0.42, 0.7, 0.54, 0.9, 0.62, 1, 0.75, 0.48, 0.82, 0.58, 0.94, 0.68, 0.5, 0.78, 0.44].map((scale, index) =>
            <i key={index} style={{ transform: `scaleY(${recording ? Math.max(0.08, Math.min(1, level * scale * 1.35)) : 0.08})` }} />
          )}
        </div>

        <button className="mic-control" type="button" onClick={() => { if (recording) stopRecording(); else void startRecording() }} disabled={status !== 'connected' || busy} aria-label={recording ? 'Stop recording and send' : 'Start recording'}>
          {recording ? <span className="stop-glyph" aria-hidden="true" /> : <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5m-4 0h8"/></svg>}
        </button>

        <div className="voice-copy">
          <h1>{recording ? 'Listening' : transcribing ? 'Transcribing' : busy ? 'Working on it' : assistantState === 'Speaking' ? 'Speaking' : 'Ready when you are'}</h1>
          {recording ? <p className="recording-time"><strong>{Math.floor(seconds / 60).toString().padStart(2, '0')}:{(seconds % 60).toString().padStart(2, '0')}</strong><span>Pause to send, or press stop</span></p> : <p>{transcribing ? 'Turning your recording into text.' : busy ? 'You can stop the response at any time.' : 'Press the microphone and speak normally.'}</p>}
        </div>

        <dl className="session-details">
          <div><dt>Voice</dt><dd>{voiceMode === 'cloud' ? voicePersona : voiceMode}</dd></div>
          <div><dt>Mode</dt><dd>Auto-send</dd></div>
          <div><dt>Privacy</dt><dd>Local only</dd></div>
        </dl>
      </section>

      <div className="conversation-column">
        {showMicIntro && permission !== 'granted' && <section className="permission-card" aria-label="Microphone permission information">
          <div className="permission-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/></svg></div>
          <div><strong>Microphone access is needed</strong><p>Your browser asks once. Audio is sent only after you start a recording.</p></div>
          <button type="button" onClick={() => setShowMicIntro(false)} aria-label="Dismiss microphone explanation"><Icon name="close" /></button>
        </section>}

        <section className="chat-card" aria-label="Chat transcript">
        <div className="chat-header">
          <div><span className="chat-title">Conversation</span><span className="chat-subtitle">Private to this browser session</span></div>
          <button className="clear" type="button" onClick={clear} disabled={status !== 'connected' || busy || recording || (entries.length === 0 && !error)}>Clear</button>
        </div>
        <div className="transcript" ref={transcript}>
          {entries.length === 0 ? <div className="empty"><strong>No conversation yet</strong><span>Speak or type to begin.</span><div className="quick-prompts">{quickPrompts.map((prompt) => <button type="button" key={prompt} onClick={() => submitText(prompt)} disabled={status !== 'connected'}>{prompt}<span aria-hidden="true">→</span></button>)}</div></div> : entries.map((entry) => <article className={`message ${entry.role}`} key={entry.id}>
            <span className="message-label">{entry.role === 'you' ? 'YOU' : 'ASSISTANT'}</span>
            <div className="bubble">{entry.role === 'nova' ? entry.text ? <Suspense fallback={<span>{entry.text}</span>}><MarkdownMessage text={entry.text} /></Suspense> : <span className="typing-dots" aria-label="Assistant is typing"><i/><i/><i/></span> : entry.text}</div>
            {entry.tools?.map((tool) => <ToolCard tool={tool} key={tool.id} />)}
            {entry.cancelled && <span className="cancelled-note">Response stopped</span>}
            {entry.role === 'nova' && entry.text && <div className="message-actions" aria-label="Response actions">
              <button type="button" onClick={() => void copyEntry(entry)} title="Copy response" aria-label="Copy response"><Icon name="copy" /></button>
              <button type="button" onClick={() => retryEntry(entry)} title="Retry response" aria-label="Retry response" disabled={busy}><Icon name="retry" /></button>
              <button type="button" onClick={() => speakEntry(entry)} title="Speak again" aria-label="Speak response"><Icon name="speak" /></button>
              <button type="button" onClick={() => deleteEntry(entry)} title="Delete response" aria-label="Delete response"><Icon name="delete" /></button>
            </div>}
          </article>)}
          <div ref={end} />
        </div>
        {error && <div className="error" role="alert"><span>!</span>{error}<button type="button" onClick={() => setError('')} aria-label="Dismiss error">×</button></div>}
        <form className="composer" onSubmit={send}>
          <textarea aria-label="Message" rows={1} maxLength={4000} placeholder={status === 'connected' ? 'Type a message…' : 'Waiting for connection…'} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onComposerKeyDown} disabled={status !== 'connected' || busy || recording} />
          {busy ? <button className="stop-button" type="button" onClick={stopGeneration}><span className="stop-square" />Stop</button> : <button type="submit" disabled={status !== 'connected' || recording || !draft.trim()}>Send <span aria-hidden="true">↗</span></button>}
        </form>
        <div className="composer-hint">Enter to send · Shift + Enter for a new line</div>
        </section>
        <footer className="footer-note">Audio is processed only after you record or send a message.</footer>
      </div>
    </main>

    {settingsOpen && <div className="drawer-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSettingsOpen(false) }}>
      <aside className="settings-drawer" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <div className="drawer-header"><div><span>Preferences</span><h2 id="settings-title">Voice settings</h2></div><button className="icon-button" type="button" onClick={() => setSettingsOpen(false)} aria-label="Close settings"><Icon name="close" /></button></div>
        <div className="drawer-content">
          <section className="setting-group"><div className="setting-heading"><strong>Response voice</strong><span>Choose how replies are read aloud.</span></div><div className="segmented-control" role="radiogroup" aria-label="Response voice">{([['cloud', 'Natural'], ['browser', 'Browser'], ['text', 'Text only']] as const).map(([value, label]) => <button type="button" role="radio" aria-checked={voiceMode === value} className={voiceMode === value ? 'selected' : ''} onClick={() => { setVoiceMode(value); playback.current?.setMuted(value === 'text'); if (value === 'text') client.current?.stopSpeaking() }} key={value}>{label}</button>)}</div></section>
          {voiceMode === 'cloud' && <section className="setting-group"><div className="setting-heading"><strong>Cloud voice</strong><span>Select the natural voice used for new responses.</span></div><div className="voice-personas" role="radiogroup" aria-label="Cloud voice"><button type="button" role="radio" aria-checked={voicePersona === 'male'} className={voicePersona === 'male' ? 'selected' : ''} onClick={() => selectVoicePersona('male')}><span className="voice-avatar" aria-hidden="true">M</span><span><strong>Male</strong><small>Troy</small></span><i /></button><button type="button" role="radio" aria-checked={voicePersona === 'female'} className={voicePersona === 'female' ? 'selected' : ''} onClick={() => selectVoicePersona('female')}><span className="voice-avatar" aria-hidden="true">F</span><span><strong>Female</strong><small>Autumn</small></span><i /></button></div></section>}
          <section className="setting-group"><label className="setting-heading" htmlFor="microphone"><strong>Microphone</strong><span>Select the input the assistant should listen to.</span></label><select id="microphone" value={selectedAudioInput} onChange={(event) => { autoSelectAudioInput.current = false; setSelectedAudioInput(event.target.value); if (!recordingRef.current) releaseMicrophone() }} disabled={recording || busy}>{audioInputs.length === 0 && <option value="">Default microphone</option>}{audioInputs.map((device, index) => <option value={device.deviceId} key={device.deviceId || index}>{device.label || `Microphone ${index + 1}`}</option>)}</select></section>
          <section className="setting-row"><div className="setting-heading"><strong>Automatic send</strong><span>A long pause ends the recording. Press the microphone again to stop immediately.</span></div><span className="always-on-badge"><i />On</span></section>
          <section className="privacy-note"><div><strong>Audio handling</strong><p>Recordings are sent to Groq for transcription. This app does not save your audio files.</p></div></section>
          {import.meta.env.DEV && <section className="diagnostics"><button className="diagnostics-toggle" type="button" onClick={() => setDebugOpen((value) => !value)}>Audio diagnostics <span>{debugOpen ? '−' : '+'}</span></button>{debugOpen && <div className="diagnostics-body"><p>Record a five-second sample without invoking the LLM.</p><button type="button" onClick={() => void recordDebugSample()} disabled={debugRecording}>{debugRecording ? 'Recording…' : 'Record sample'}</button>{debugUrl && <audio controls src={debugUrl} />}{debugStats && <small>{debugStats}</small>}{debugTranscript && <p><strong>Transcript:</strong> {debugTranscript}</p>}<small>Input: {activeInput || 'not sampled'}{inputSampleRate ? ` · ${inputSampleRate} Hz` : ''}</small></div>}</section>}
        </div>
      </aside>
    </div>}
  </div>
}
