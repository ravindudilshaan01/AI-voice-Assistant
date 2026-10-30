import { useEffect, useRef, useState, type FormEvent } from 'react'
import { NovaWebSocket, type ConnectionState, type ServerEvent } from './services/websocket'
import { AudioPlayback, type PlaybackStatus } from './services/audioPlayback'

type Entry = { id: string; role: 'you' | 'nova'; text: string }
const wsUrl = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws'
const maxAudioBytes = 10 * 1024 * 1024
const audioTypes = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4', 'audio/webm', 'audio/ogg']

export default function App() {
  const [status, setStatus] = useState<ConnectionState>('connecting')
  const [entries, setEntries] = useState<Entry[]>([])
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [toolActivity, setToolActivity] = useState('')
  const [playbackStatus, setPlaybackStatus] = useState<PlaybackStatus>('idle')
  const [muted, setMuted] = useState(false)
  const [recording, setRecording] = useState(false)
  const [handsFree, setHandsFree] = useState(true)
  const [audioInputs, setAudioInputs] = useState<MediaDeviceInfo[]>([])
  const [selectedAudioInput, setSelectedAudioInput] = useState('')
  const [seconds, setSeconds] = useState(0)
  const [level, setLevel] = useState(0)
  const client = useRef<NovaWebSocket | null>(null)
  const playback = useRef<AudioPlayback | null>(null)
  const mutedRef = useRef(false)
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const audioContext = useRef<AudioContext | null>(null)
  const animation = useRef<number | null>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)
  const end = useRef<HTMLDivElement | null>(null)
  const busyRef = useRef(false)
  const recordingRef = useRef(false)
  const handsFreeRef = useRef(true)
  const handsFreeArmed = useRef(false)
  const playbackStatusRef = useRef<PlaybackStatus>('idle')
  const heardSpeech = useRef(false)
  const lastSpeechAt = useRef(0)
  const voiceCandidateAt = useRef(0)
  const startRecordingRef = useRef<() => Promise<void>>(async () => undefined)

  const setProcessing = (value: boolean) => { busyRef.current = value; setBusy(value) }
  const releaseMicrophone = () => {
    stream.current?.getTracks().forEach((track) => track.stop())
    stream.current = null
    if (animation.current !== null) cancelAnimationFrame(animation.current)
    animation.current = null
    if (timer.current) clearInterval(timer.current)
    timer.current = null
    void audioContext.current?.close()
    audioContext.current = null
    setLevel(0)
    recordingRef.current = false
    setRecording(false)
  }

  const refreshAudioInputs = async (preferredDeviceId?: string) => {
    if (!navigator.mediaDevices?.enumerateDevices) return
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter((device) => device.kind === 'audioinput')
    setAudioInputs(devices)
    setSelectedAudioInput((current) => {
      if (preferredDeviceId && devices.some((device) => device.deviceId === preferredDeviceId)) return preferredDeviceId
      if (current && devices.some((device) => device.deviceId === current)) return current
      const hardware = devices.find((device) => device.deviceId !== 'default' && device.deviceId !== 'communications')
      return hardware?.deviceId || devices[0]?.deviceId || ''
    })
  }

  useEffect(() => {
    const updatePlaybackStatus = (next: PlaybackStatus) => {
      playbackStatusRef.current = next
      setPlaybackStatus(next)
    }
    const player = new AudioPlayback(updatePlaybackStatus, setError)
    playback.current = player
    const handleEvent = (event: ServerEvent) => {
      if (event.type === 'transcript') {
        setEntries((current) => [...current, { id: `user-${event.requestId}`, role: 'you', text: event.text }])
      } else if (event.type === 'chat_started') {
        player.start(event.requestId, event.speak !== false)
        setEntries((current) => [...current, { id: `nova-${event.requestId}`, role: 'nova', text: '' }])
      } else if (event.type === 'chat_delta') {
        setEntries((current) => current.map((entry) => entry.id === `nova-${event.requestId}` ? { ...entry, text: entry.text + event.text } : entry))
      } else if (event.type === 'chat_done') {
        setProcessing(false)
        setToolActivity('')
      } else if (event.type === 'tool_activity') {
        setToolActivity(event.state === 'started' ? event.label : '')
      } else if (event.type === 'tts_fallback') {
        player.enqueueFallback(event.responseId, event.index, event.text)
      } else if (event.type === 'tts_complete') {
        player.complete(event.responseId, event.segmentCount)
      } else if (event.type === 'tts_stopped') {
        player.stop()
      } else if (event.type === 'error') {
        player.stop()
        setError(event.message)
        setProcessing(false)
        setToolActivity('')
        if (event.requestId) setEntries((current) => current.filter((entry) => entry.id !== `nova-${event.requestId}` || entry.text.length > 0))
      }
    }
    const service = new NovaWebSocket(wsUrl, {
      onState: (state) => { setStatus(state); if (state === 'disconnected') { setProcessing(false); setToolActivity(''); player.stop() } },
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

  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth' }) }, [entries])

  const send = (event: FormEvent) => {
    event.preventDefault()
    const text = draft.trim()
    if (!text || busyRef.current || recording) return
    playback.current?.stop()
    client.current?.stopSpeaking()
    const requestId = crypto.randomUUID()
    if (!client.current?.sendMessage(text, requestId, !mutedRef.current)) {
      setError('Connection unavailable. Your message was not sent.')
      return
    }
    setProcessing(true)
    setEntries((current) => [...current, { id: `user-${requestId}`, role: 'you', text }])
    setDraft('')
    setError('')
  }

  const startRecording = async () => {
    if (busyRef.current || recording || status !== 'connected') return
    playback.current?.stop()
    client.current?.stopSpeaking()
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setError('Microphone recording is unavailable in this browser or context. Use localhost or HTTPS.')
      return
    }
    const mimeType = audioTypes.find((type) => MediaRecorder.isTypeSupported(type))
    if (!mimeType) {
      setError('This browser cannot record a format supported by Groq. Try Chrome, Edge, or Firefox.')
      return
    }
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
          },
        })
        stream.current = media
        void refreshAudioInputs(media.getAudioTracks()[0]?.getSettings().deviceId)
      }
      const mediaRecorder = new MediaRecorder(media, { mimeType })
      recorder.current = mediaRecorder
      const chunks: Blob[] = []
      let totalBytes = 0
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
        if (timer.current) clearInterval(timer.current)
        timer.current = null
        recordingRef.current = false
        setRecording(false)
        if (!handsFreeRef.current) releaseMicrophone()
        recorder.current = null
        if (failed) return
        if (totalBytes === 0) { setError('The recording was empty. Please try again.'); return }
        if (totalBytes > maxAudioBytes) { setError('Recording is too large. Please record a shorter message.'); return }
        const actualType = mediaRecorder.mimeType || chunks[0].type
        const blob = new Blob(chunks, { type: actualType })
        setProcessing(true)
        setError('')
        try {
          if (!await client.current?.sendAudio(blob, actualType, requestId, !mutedRef.current)) {
            setProcessing(false)
            setError('Connection unavailable. Recording was not sent.')
          }
        } catch {
          setProcessing(false)
          setError('Could not send the recording. Please try again.')
        }
      }
      mediaRecorder.start(250)
      setSeconds(0)
      recordingRef.current = true
      heardSpeech.current = false
      lastSpeechAt.current = performance.now()
      setRecording(true)
      const started = Date.now()
      timer.current = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 250)

      if (!audioContext.current) {
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
          analyser.getByteTimeDomainData(data)
          let sumSquares = 0
          for (const sample of data) {
            const centered = (sample - 128) / 128
            sumSquares += centered * centered
          }
          const nextLevel = Math.min(1, Math.sqrt(sumSquares / data.length) * 8)
          setLevel(nextLevel)
          const now = performance.now()
          if (nextLevel >= 0.045) {
            heardSpeech.current = true
            lastSpeechAt.current = now
            if (!voiceCandidateAt.current) voiceCandidateAt.current = now
            const playing = ['preparing', 'speaking', 'browser'].includes(playbackStatusRef.current)
            if (handsFreeRef.current && handsFreeArmed.current && playing && now - voiceCandidateAt.current > 180 && !recordingRef.current) {
              playback.current?.stop()
              client.current?.stopSpeaking()
              voiceCandidateAt.current = 0
              void startRecordingRef.current()
            }
          } else {
            voiceCandidateAt.current = 0
          }
          if (recordingRef.current && heardSpeech.current && now - lastSpeechAt.current >= 900) recorder.current?.stop()
          animation.current = requestAnimationFrame(updateLevel)
        }
        updateLevel()
      }
    } catch (cause) {
      releaseMicrophone()
      if (cause instanceof DOMException && (cause.name === 'NotAllowedError' || cause.name === 'PermissionDeniedError')) {
        setError('Microphone permission was denied. Allow microphone access in your browser and try again.')
      } else {
        setError('Could not start the microphone. Check your device and try again.')
      }
    }
  }

  startRecordingRef.current = startRecording

  const stopRecording = () => { if (recorder.current?.state === 'recording') recorder.current.stop() }
  const clear = () => {
    if (busyRef.current || recording) return
    if (!client.current?.clear()) { setError('Reconnect before clearing the conversation.'); return }
    playback.current?.stop()
    setPlaybackStatus(mutedRef.current ? 'muted' : 'idle')
    setEntries([])
    setError('')
  }

  const stopSpeaking = () => {
    playback.current?.stop()
    client.current?.stopSpeaking()
  }
  const toggleMute = () => {
    const next = !mutedRef.current
    mutedRef.current = next
    setMuted(next)
    playback.current?.setMuted(next)
    if (next) client.current?.stopSpeaking()
  }

  const toggleHandsFree = () => {
    const next = !handsFreeRef.current
    handsFreeRef.current = next
    setHandsFree(next)
    if (!next) {
      handsFreeArmed.current = false
      if (!recordingRef.current) releaseMicrophone()
    }
  }

  useEffect(() => {
    handsFreeRef.current = handsFree
    if (!handsFree || !handsFreeArmed.current || status !== 'connected' || busy || recording) return
    if (playbackStatus === 'preparing' || playbackStatus === 'speaking' || playbackStatus === 'browser') return
    const restart = window.setTimeout(() => {
      if (handsFreeRef.current && !busyRef.current && !recordingRef.current) void startRecordingRef.current()
    }, 350)
    return () => window.clearTimeout(restart)
  }, [handsFree, status, busy, recording, playbackStatus])

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-mark">✦</span><span>NOVA <strong>AI</strong></span></div>
      <div className={`status ${status}`}><span className="status-dot" />{status}</div>
    </header>
    <main className="main">
      <section className="hero" aria-label="Assistant overview">
        <div className="orb-wrap"><div className={`orb-ring ${recording ? 'active' : ''}`}><button className="orb" type="button" onClick={() => { if (recording) stopRecording(); else { handsFreeArmed.current = handsFreeRef.current; void startRecording() } }} disabled={status !== 'connected' || busy} aria-label={recording ? 'Stop recording' : 'Start recording'} title={recording ? 'Stop recording' : 'Start recording'}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5m-4 0h8"/></svg></button></div></div>
        <div className="eyebrow">YOUR SPACE TO THINK OUT LOUD</div>
        <h1>Meet NOVA.</h1>
        <p aria-live="polite">{recording ? `Recording ${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')} · tap to stop` : toolActivity ? `${toolActivity}…` : busy ? 'NOVA is thinking…' : 'Tap the microphone or type a message.'}</p>
        <div className="playback-controls"><span className="playback-status" aria-live="polite">{muted ? 'Voice muted' : playbackStatus === 'preparing' ? 'Preparing voice' : playbackStatus === 'speaking' ? 'Speaking' : playbackStatus === 'browser' ? 'Browser voice' : playbackStatus === 'stopped' ? 'Speech stopped' : 'Voice ready'}</span><button type="button" onClick={stopSpeaking} disabled={playbackStatus !== 'preparing' && playbackStatus !== 'speaking' && playbackStatus !== 'browser'}>Stop Speaking</button><button type="button" onClick={toggleMute} aria-pressed={muted}>{muted ? 'Unmute' : 'Mute'}</button><button type="button" onClick={toggleHandsFree} aria-pressed={handsFree}>{handsFree ? 'Hands-free on' : 'Hands-free off'}</button></div>
        <label className="microphone-select">Microphone <select value={selectedAudioInput} onChange={(event) => { setSelectedAudioInput(event.target.value); if (!recordingRef.current) releaseMicrophone() }} disabled={recording || busy}>{audioInputs.length === 0 && <option value="">Default microphone</option>}{audioInputs.map((device, index) => <option value={device.deviceId} key={device.deviceId || index}>{device.label || `Microphone ${index + 1}`}</option>)}</select></label>
        {recording && <div className="audio-meter" aria-label="Microphone activity"><span style={{ height: `${Math.max(15, level * 90)}%` }} /><span style={{ height: `${Math.max(25, level * 130)}%` }} /><span style={{ height: `${Math.max(20, level * 110)}%` }} /><span style={{ height: `${Math.max(15, level * 95)}%` }} /></div>}
      </section>
      <section className="chat-card" aria-label="Chat transcript">
        <div className="chat-header"><div><span className="chat-title">Conversation</span><span className="chat-subtitle">Your local voice and text chat</span></div><button className="clear" type="button" onClick={clear} disabled={status !== 'connected' || busy || recording || (entries.length === 0 && !error)}>Clear</button></div>
        <div className="transcript" aria-live="polite">
          {entries.length === 0 ? <div className="empty"><span className="empty-icon">✧</span><strong>Nothing here yet</strong><span>Speak or type to start a conversation.</span></div> : entries.map((entry) => <div className={`message ${entry.role}`} key={entry.id}><span className="message-label">{entry.role === 'you' ? 'YOU' : 'NOVA'}</span><div className="bubble">{entry.text || '…'}</div></div>)}
          <div ref={end} />
        </div>
        {error && <div className="error" role="alert">{error}</div>}
        <form className="composer" onSubmit={send}><input aria-label="Message" maxLength={4000} placeholder={status === 'connected' ? 'Type a message…' : 'Waiting for connection…'} value={draft} onChange={(event) => setDraft(event.target.value)} disabled={status !== 'connected' || busy || recording} /><button type="submit" disabled={status !== 'connected' || busy || recording || !draft.trim()} aria-label="Send message">Send <span aria-hidden="true">↗</span></button></form>
      </section>
      <p className="footer-note">PHASE 07 <span>·</span> REAL-TIME GROQ VOICE ASSISTANT</p>
    </main>
  </div>
}
