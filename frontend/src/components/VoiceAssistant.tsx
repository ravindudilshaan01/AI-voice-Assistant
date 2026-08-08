import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useVoiceSocket, type AssistantStatus } from "../hooks/useVoiceSocket";
import "./VoiceAssistant.css";

const STATUS_COPY: Record<AssistantStatus, { label: string; hint: string }> = {
  idle: { label: "Ready", hint: "Tap the orb to speak" },
  connecting: { label: "Connecting...", hint: "Opening a secure voice channel" },
  recording: { label: "Listening...", hint: "Tap the orb when you are finished" },
  thinking: { label: "Thinking...", hint: "Nova is working on your request" },
  speaking: { label: "Speaking...", hint: "Playing Nova's response" },
  error: { label: "Try again", hint: "Tap the orb or send a new message" },
};

const QUICK_COMMANDS = [
  { icon: "weather", label: "Weather", prompt: "What's the weather in Colombo?" },
  { icon: "search", label: "Quick search", prompt: "What are today's top technology headlines?" },
  { icon: "calendar", label: "Reminder", prompt: "Remind me to review my notes tomorrow at 6 PM." },
];

const EMPTY_HISTORY = [
  { label: "Weather", icon: "weather" },
  { label: "Web search", icon: "search" },
  { label: "Reminders", icon: "calendar" },
  { label: "General Q&A", icon: "spark" },
];

function Icon({ name, size = 19 }: { name: string; size?: number }) {
  const paths: Record<string, JSX.Element> = {
    spark: <path d="m12 2 1.5 4.6L18 8l-4.5 1.4L12 14l-1.5-4.6L6 8l4.5-1.4L12 2Zm6 12 .8 2.2L21 17l-2.2.8L18 20l-.8-2.2L15 17l2.2-.8L18 14ZM5 15l1 3 3 1-3 1-1 3-1-3-3-1 3-1 1-3Z" />,
    weather: <path d="M6.5 18h10a4 4 0 0 0 .5-7.97A5.6 5.6 0 0 0 6.2 8.4 4.8 4.8 0 0 0 6.5 18Z" />,
    search: <path d="m20.5 20.5-4.2-4.2M18.5 10.5a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z" />,
    calendar: <path d="M6 2v4M18 2v4M3 9h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z" />,
    mic: <path d="M12 2a3.2 3.2 0 0 0-3.2 3.2v6.6a3.2 3.2 0 0 0 6.4 0V5.2A3.2 3.2 0 0 0 12 2ZM5 11.5a7 7 0 0 0 14 0M12 18.5V22M8.5 22h7" />,
    stop: <rect x="7" y="7" width="10" height="10" rx="2" />,
    send: <path d="m21 3-7.3 18-3.2-7.4L3 10.4 21 3Zm-10.5 10.6L15 9" />,
    plus: <path d="M12 5v14M5 12h14" />,
    moon: <path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5 8.5 8.5 0 1 0 20.5 14.2Z" />,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" /></>,
    keyboard: <><rect x="2" y="5" width="20" height="14" rx="2" /><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 13h.01M10 13h.01M14 13h.01M18 13h.01M8 16h8" /></>,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    bolt: <path d="m13 2-8 12h7l-1 8 8-12h-7l1-8Z" />,
    history: <path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2" />,
  };

  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={name === "spark" ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

function getIntent(text: string, toolName?: string | null): string {
  if (toolName === "get_weather") return "Weather lookup";
  if (toolName === "schedule_event") return "Schedule event";
  if (toolName === "web_search") return "Web research";

  const normalized = text.toLowerCase();
  if (/\b(weather|forecast|temperature|rain|sunny|wind)\b/.test(normalized)) return "Weather lookup";
  if (/\b(remind|reminder|schedule|appointment|meeting|calendar)\b/.test(normalized)) return "Schedule event";
  if (/\b(search|find|latest|today|news|current|look up|research)\b/.test(normalized)) return "Web research";
  return text ? "General assistance" : "Waiting for command";
}

function VoiceOrb({ status, onClick }: { status: AssistantStatus; onClick: () => void }) {
  const disabled = status === "connecting" || status === "thinking" || status === "speaking";
  const particles = useMemo(
    () => Array.from({ length: 28 }, (_, index) => ({
      left: `${12 + ((index * 31) % 76)}%`,
      top: `${18 + ((index * 47) % 64)}%`,
      delay: `${(index % 9) * -0.18}s`,
    })),
    []
  );

  return (
    <button className={`voice-orb voice-orb--${status}`} type="button" onClick={onClick} disabled={disabled} aria-label={status === "recording" ? "Stop recording" : "Start voice command"}>
      <span className="orb-halo orb-halo--outer" />
      <span className="orb-halo orb-halo--inner" />
      <span className="orb-sphere" />
      <span className="orb-gloss" />
      <span className="orb-equator" />
      <svg className="energy-wave" viewBox="0 0 420 150" preserveAspectRatio="none" aria-hidden="true">
        <path className="wave-line wave-line--cyan" d="M0 76 C38 76 50 66 72 74 S108 98 132 70 S167 43 193 79 S226 102 250 70 S285 55 307 76 S350 90 420 73" />
        <path className="wave-line wave-line--violet" d="M0 79 C42 85 60 94 84 74 S118 48 145 78 S180 106 208 72 S242 55 270 80 S312 99 340 69 S380 60 420 78" />
        <path className="wave-line wave-line--pink" d="M0 75 C48 69 66 58 91 78 S129 102 157 72 S195 51 220 82 S259 99 288 70 S325 53 349 79 S390 88 420 75" />
      </svg>
      <span className="orb-particles" aria-hidden="true">
        {particles.map((particle, index) => <i key={index} style={{ left: particle.left, top: particle.top, animationDelay: particle.delay }} />)}
      </span>
      <span className="orb-action"><Icon name={status === "recording" ? "stop" : "mic"} size={24} /></span>
    </button>
  );
}

export function VoiceAssistant() {
  const {
    status,
    messages,
    currentToolActivity,
    lastToolName,
    errorMessage,
    startRecording,
    stopRecording,
    sendTextMessage,
    clearConversation,
    dismissError,
  } = useVoiceSocket();

  const [draft, setDraft] = useState("");
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [lastInputMode, setLastInputMode] = useState<"Voice" | "Text">("Voice");
  const inputRef = useRef<HTMLInputElement>(null);

  const userMessages = messages.filter((message) => message.role === "user");
  const assistantMessages = messages.filter((message) => message.role === "assistant");
  const latestUser = userMessages[userMessages.length - 1]?.text ?? "";
  const latestAssistant = assistantMessages[assistantMessages.length - 1]?.text ?? "";
  const recentCommands = userMessages.slice(-6).reverse();
  const isBusy = status === "connecting" || status === "thinking" || status === "speaking";

  useEffect(() => {
    if (status !== "recording") {
      setRecordingSeconds(0);
      return;
    }
    const timer = window.setInterval(() => setRecordingSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [status]);

  const handleOrb = () => {
    if (status === "recording") {
      stopRecording();
      return;
    }
    if (!isBusy) {
      setLastInputMode("Voice");
      void startRecording();
    }
  };

  const submitMessage = (event: FormEvent) => {
    event.preventDefault();
    const value = draft.trim();
    if (!value || isBusy || status === "recording") return;
    setDraft("");
    setLastInputMode("Text");
    void sendTextMessage(value);
  };

  const runQuickCommand = (prompt: string) => {
    if (isBusy || status === "recording") return;
    setLastInputMode("Text");
    void sendTextMessage(prompt);
  };

  return (
    <div className="command-page">
      <div className="background-beam background-beam--one" />
      <div className="background-beam background-beam--two" />

      <main className="command-console">
        <header className="console-header">
          <div className="console-brand"><span>AI</span> Assistance</div>
          <div className="session-user"><span className="user-avatar">N</span><div><strong>Nova Workspace</strong><small>Personal assistant</small></div></div>
          <div className="header-actions">
            <button type="button" aria-label="Start new conversation" onClick={clearConversation} disabled={isBusy || status === "recording"}><Icon name="plus" /></button>
            <button type="button" aria-label="Dark appearance"><Icon name="moon" /></button>
            <span className="premium-badge"><Icon name="bolt" size={13} /> LIVE</span>
          </div>
        </header>

        <section className="console-body">
          <aside className="history-panel dashboard-panel">
            <div className="panel-title"><div><span>Activity</span><h2>Previous commands</h2></div><Icon name="history" size={20} /></div>
            <div className="history-grid">
              {recentCommands.length > 0 ? recentCommands.map((message, index) => (
                <article className="history-card" key={message.id}>
                  <div className="history-card__icon"><Icon name={getIntent(message.text).includes("Weather") ? "weather" : getIntent(message.text).includes("Schedule") ? "calendar" : "search"} size={15} /></div>
                  <strong>{getIntent(message.text)}</strong>
                  <p>{message.text}</p>
                  <div className="history-meter"><span style={{ width: `${48 + ((message.text.length + index * 11) % 45)}%` }} /></div>
                </article>
              )) : EMPTY_HISTORY.map((item, index) => (
                <article className="history-card history-card--empty" key={item.label}>
                  <div className="history-card__icon"><Icon name={item.icon} size={15} /></div>
                  <strong>{item.label}</strong>
                  <p>Ready when you are</p>
                  <div className="history-meter"><span style={{ width: `${34 + index * 12}%` }} /></div>
                </article>
              ))}
            </div>
            <button className="clear-history" type="button" onClick={clearConversation} disabled={!messages.length || isBusy}><Icon name="plus" size={15} /> New session</button>
          </aside>

          <section className="voice-stage">
            <div className="stage-grid" />
            <div className="stage-kicker"><span /> NEURAL VOICE INTERFACE <span /></div>
            <VoiceOrb status={status} onClick={handleOrb} />
            <div className="voice-copy" aria-live="polite">
              <p className="heard-command">{latestUser ? `“${latestUser}”` : "Ask anything, naturally"}</p>
              <h1>{STATUS_COPY[status].label}</h1>
              <p>{currentToolActivity ?? STATUS_COPY[status].hint}</p>
              {status === "recording" && <span className="recording-clock">00:{recordingSeconds.toString().padStart(2, "0")}</span>}
            </div>

            {latestAssistant && status !== "recording" && (
              <div className="response-preview"><span>Nova</span><p>{latestAssistant}</p></div>
            )}

            <form className="command-input" onSubmit={submitMessage}>
              <Icon name="keyboard" size={18} />
              <input ref={inputRef} value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Type a command..." disabled={isBusy || status === "recording"} maxLength={2000} aria-label="Type a command" />
              <button type="submit" disabled={!draft.trim() || isBusy || status === "recording"} aria-label="Send command"><Icon name="send" size={17} /></button>
            </form>

            {errorMessage && <div className="stage-error" role="alert"><span>{errorMessage}</span><button type="button" onClick={dismissError}><Icon name="close" size={15} /></button></div>}
          </section>

          <aside className="details-panel dashboard-panel">
            <div className="panel-title"><div><span>Live analysis</span><h2>Command details</h2></div><span className={`live-dot live-dot--${status}`} /></div>
            <dl className="command-details">
              <div><dt>Input mode</dt><dd>{lastInputMode}</dd></div>
              <div><dt>Detected intent</dt><dd>{getIntent(latestUser, lastToolName)}</dd></div>
              <div><dt>Session results</dt><dd>{assistantMessages.length} responses</dd></div>
              <div><dt>Active process</dt><dd className="accent-value">{currentToolActivity ?? STATUS_COPY[status].label}</dd></div>
            </dl>

            <div className="quick-heading"><span>Quick launch</span><small>Suggested commands</small></div>
            <div className="quick-list">
              {QUICK_COMMANDS.map((item) => (
                <button type="button" key={item.label} onClick={() => runQuickCommand(item.prompt)} disabled={isBusy || status === "recording"}>
                  <span className={`quick-icon quick-icon--${item.icon}`}><Icon name={item.icon} size={17} /></span>
                  <span><strong>{item.label}</strong><small>{item.prompt}</small></span>
                  <i>›</i>
                </button>
              ))}
            </div>

            <div className="system-card">
              <div className="system-card__visual"><span /><span /><span /><span /><span /></div>
              <div><span>System status</span><strong>All services operational</strong><small>Gemini · Whisper · Edge Voice</small></div>
            </div>
          </aside>
        </section>

        <nav className="console-nav" aria-label="Assistant controls">
          <button type="button" onClick={clearConversation} aria-label="New conversation"><Icon name="plus" /></button>
          <button type="button" className="console-nav__active" onClick={handleOrb} disabled={isBusy} aria-label="Voice control"><Icon name="mic" /></button>
          <button type="button" onClick={() => inputRef.current?.focus()} aria-label="Type a command"><Icon name="keyboard" /></button>
          <button type="button" aria-label="Assistant settings"><Icon name="settings" /></button>
        </nav>
      </main>
    </div>
  );
}
