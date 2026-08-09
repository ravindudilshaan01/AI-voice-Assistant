import { useCallback, useEffect, useRef, useState } from "react";

export type AssistantStatus =
  | "idle"
  | "connecting"
  | "recording"
  | "thinking"
  | "speaking"
  | "error";

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
}

interface RecognitionResultLike {
  readonly 0: { transcript: string };
  readonly isFinal: boolean;
}

interface RecognitionEventLike extends Event {
  readonly results: ArrayLike<RecognitionResultLike>;
}

interface BrowserSpeechRecognition {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  maxAlternatives: number;
  onresult: ((event: RecognitionEventLike) => void) | null;
  onerror: ((event: Event & { error?: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

type SpeechRecognitionConstructor = new () => BrowserSpeechRecognition;

function getSpeechRecognitionConstructor(): SpeechRecognitionConstructor | undefined {
  const speechWindow = window as typeof window & {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;
}

interface UseVoiceSocketResult {
  status: AssistantStatus;
  messages: Message[];
  currentToolActivity: string | null;
  lastToolName: string | null;
  liveTranscript: string;
  liveTranscriptionSupported: boolean;
  inputLevel: number;
  audioInputDevices: MediaDeviceInfo[];
  selectedAudioInputId: string;
  alwaysListening: boolean;
  errorMessage: string | null;
  setSelectedAudioInputId: (deviceId: string) => void;
  setAlwaysListening: (enabled: boolean) => void;
  startRecording: () => Promise<void>;
  stopRecording: () => void;
  sendTextMessage: (text: string) => Promise<void>;
  clearConversation: () => void;
  dismissError: () => void;
}

const WS_URL = import.meta.env.VITE_WS_URL ?? "ws://localhost:8000/ws/voice";

const TOOL_LABELS: Record<string, string> = {
  get_weather: "Checking the weather",
  web_search: "Searching the web",
  schedule_event: "Creating your reminder",
};

let fallbackId = 0;

function createMessageId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  fallbackId += 1;
  return `message-${Date.now()}-${fallbackId}`;
}

export function useVoiceSocket(): UseVoiceSocketResult {
  const [status, setStatus] = useState<AssistantStatus>("idle");
  const [messages, setMessages] = useState<Message[]>([]);
  const [currentToolActivity, setCurrentToolActivity] = useState<string | null>(null);
  const [lastToolName, setLastToolName] = useState<string | null>(null);
  const [liveTranscript, setLiveTranscript] = useState("");
  const [liveTranscriptionSupported] = useState(
    () => typeof window !== "undefined" && Boolean(getSpeechRecognitionConstructor())
  );
  const [inputLevel, setInputLevel] = useState(0);
  const [audioInputDevices, setAudioInputDevices] = useState<MediaDeviceInfo[]>([]);
  const [selectedAudioInputId, setSelectedAudioInputId] = useState("");
  const [alwaysListening, setAlwaysListening] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const sourceStreamRef = useRef<MediaStream | null>(null);
  const speechRecognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioPlayingRef = useRef(false);
  const audioCleanupRef = useRef<(() => void) | null>(null);
  const terminalErrorRef = useRef(false);
  const audioContextRef = useRef<AudioContext | null>(null);
  const levelFrameRef = useRef<number | null>(null);
  const statusRef = useRef<AssistantStatus>("idle");
  const alwaysListeningRef = useRef(true);
  const recordingHeardSpeechRef = useRef(false);
  const recordingStartedAtRef = useRef(0);
  const lastSpeechAtRef = useRef(0);
  const voiceCandidateAtRef = useRef(0);
  const discardRecordingRef = useRef(false);
  const startRecordingRef = useRef<() => Promise<void>>(async () => undefined);
  const stopRecordingRef = useRef<() => void>(() => undefined);

  const refreshAudioInputs = useCallback(async (preferredDeviceId?: string) => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter(
      (device) => device.kind === "audioinput"
    );
    setAudioInputDevices(devices);
    setSelectedAudioInputId((current) => {
      const hardwareDevice = devices.find(
        (device) => device.deviceId !== "default" && device.deviceId !== "communications"
      );
      if (preferredDeviceId && devices.some((device) => device.deviceId === preferredDeviceId)) {
        return preferredDeviceId === "default" && hardwareDevice
          ? hardwareDevice.deviceId
          : preferredDeviceId;
      }
      if (current && devices.some((device) => device.deviceId === current)) return current;
      return hardwareDevice?.deviceId ?? devices[0]?.deviceId ?? "";
    });
  }, []);

  const stopInputMeter = useCallback(() => {
    if (levelFrameRef.current !== null) {
      window.cancelAnimationFrame(levelFrameRef.current);
      levelFrameRef.current = null;
    }
    const context = audioContextRef.current;
    audioContextRef.current = null;
    if (context && context.state !== "closed") void context.close();
    setInputLevel(0);
  }, []);

  const startInputMeter = useCallback((stream: MediaStream) => {
    stopInputMeter();
    const context = new AudioContext();
    void context.resume();
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.72;
    source.connect(analyser);
    audioContextRef.current = context;
    const samples = new Uint8Array(analyser.fftSize);

    const updateLevel = () => {
      analyser.getByteTimeDomainData(samples);
      let sumSquares = 0;
      for (const sample of samples) {
        const centered = (sample - 128) / 128;
        sumSquares += centered * centered;
      }
      const rms = Math.sqrt(sumSquares / samples.length);
      const level = Math.min(1, rms * 8);
      setInputLevel(level);

      const now = performance.now();
      const voiceThreshold = statusRef.current === "speaking" ? 0.1 : 0.045;
      const voiceDetected = level >= voiceThreshold;
      if (voiceDetected) {
        if (!voiceCandidateAtRef.current) voiceCandidateAtRef.current = now;
        lastSpeechAtRef.current = now;
        if (statusRef.current === "recording") {
          recordingHeardSpeechRef.current = true;
        }

        // Sustained speech while Nova is talking is a barge-in. Stop playback
        // immediately and begin a new utterance on the already-open mic/socket.
        if (
          statusRef.current === "speaking" &&
          now - voiceCandidateAtRef.current >= 120 &&
          !mediaRecorderRef.current
        ) {
          statusRef.current = "connecting";
          audioRef.current?.pause();
          audioCleanupRef.current?.();
          voiceCandidateAtRef.current = 0;
          void startRecordingRef.current();
        }
      } else {
        voiceCandidateAtRef.current = 0;
      }

      if (statusRef.current === "recording") {
        if (
          recordingHeardSpeechRef.current &&
          now - lastSpeechAtRef.current >= 850
        ) {
          stopRecordingRef.current();
        } else if (
          !recordingHeardSpeechRef.current &&
          now - recordingStartedAtRef.current >= 30000
        ) {
          // Bound memory while waiting indefinitely; restart without sending silence.
          discardRecordingRef.current = true;
          stopRecordingRef.current();
        }
      }
      levelFrameRef.current = window.requestAnimationFrame(updateLevel);
    };
    updateLevel();
  }, [stopInputMeter]);

  const addMessage = useCallback((role: Message["role"], text: string) => {
    setMessages((previous) => [
      ...previous,
      { id: createMessageId(), role, text },
    ]);
  }, []);

  const ensureSocket = useCallback((): Promise<WebSocket> => {
    return new Promise((resolve, reject) => {
      const existing = socketRef.current;
      if (existing?.readyState === WebSocket.OPEN) {
        resolve(existing);
        return;
      }

      if (existing) {
        existing.onclose = null;
        existing.close();
      }

      setStatus("connecting");
      statusRef.current = "connecting";
      const socket = new WebSocket(WS_URL);
      socket.binaryType = "arraybuffer";
      socketRef.current = socket;

      socket.onopen = () => resolve(socket);

      socket.onerror = () => {
        terminalErrorRef.current = true;
        setStatus("error");
        statusRef.current = "error";
        setErrorMessage("Could not connect to the assistant server.");
        if (socketRef.current === socket) socketRef.current = null;
        reject(new Error("WebSocket connection failed"));
      };

      socket.onclose = () => {
        if (socketRef.current === socket) socketRef.current = null;
        setCurrentToolActivity(null);
        if (!terminalErrorRef.current) {
          setStatus("idle");
          statusRef.current = "idle";
        }
      };

      socket.onmessage = async (event) => {
        if (typeof event.data === "string") {
          let payload: Record<string, unknown>;
          try {
            payload = JSON.parse(event.data) as Record<string, unknown>;
          } catch {
            terminalErrorRef.current = true;
            setStatus("error");
            setErrorMessage("The server returned an invalid response.");
            return;
          }

          switch (payload.type) {
            case "transcript":
              addMessage("user", String(payload.text ?? ""));
              setLiveTranscript("");
              setCurrentToolActivity(null);
              setLastToolName(null);
              setStatus("thinking");
              statusRef.current = "thinking";
              break;

            case "tool_call": {
              const toolName = String(payload.name ?? "");
              setLastToolName(toolName);
              setCurrentToolActivity(TOOL_LABELS[toolName] ?? "Using a tool");
              break;
            }

            case "tool_result":
              setCurrentToolActivity(null);
              break;

            case "assistant_text":
              addMessage("assistant", String(payload.text ?? ""));
              break;

            case "tts_error":
              setErrorMessage(String(payload.message ?? "Voice playback is unavailable."));
              break;

            case "error":
              terminalErrorRef.current = true;
              setCurrentToolActivity(null);
              setStatus("error");
              statusRef.current = "error";
              setErrorMessage(String(payload.message ?? "Something went wrong."));
              break;

            case "done":
              setCurrentToolActivity(null);
              if (!audioPlayingRef.current && !terminalErrorRef.current) {
                setStatus("idle");
                statusRef.current = "idle";
              }
              break;
          }
          return;
        }

        audioRef.current?.pause();
        audioCleanupRef.current?.();
        audioPlayingRef.current = true;
        setStatus("speaking");
        statusRef.current = "speaking";

        const blob = new Blob([event.data], { type: "audio/mpeg" });
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audioRef.current = audio;

        let cleanedUp = false;
        const cleanup = () => {
          if (cleanedUp) return;
          cleanedUp = true;
          if (audioRef.current === audio) audioRef.current = null;
          if (audioCleanupRef.current === cleanup) audioCleanupRef.current = null;
          audioPlayingRef.current = false;
          URL.revokeObjectURL(url);
          if (!terminalErrorRef.current) {
            setStatus("idle");
            statusRef.current = "idle";
          }
        };
        audioCleanupRef.current = cleanup;

        audio.onended = cleanup;
        audio.onerror = () => {
          setErrorMessage("Your answer is ready, but audio playback failed.");
          cleanup();
        };

        try {
          await audio.play();
        } catch {
          setErrorMessage("Your browser blocked audio playback.");
          cleanup();
        }
      };
    });
  }, [addMessage]);

  const startRecording = useCallback(async () => {
    if (audioPlayingRef.current) {
      audioRef.current?.pause();
      audioCleanupRef.current?.();
    }
    setErrorMessage(null);
    setLiveTranscript("");
    terminalErrorRef.current = false;

    let socket: WebSocket;
    try {
      socket = await ensureSocket();
    } catch {
      return;
    }

    let stream = sourceStreamRef.current;
    const reusableTrack = stream?.getAudioTracks()[0];
    if (!stream || !reusableTrack || reusableTrack.readyState !== "live") {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            deviceId: selectedAudioInputId ? { exact: selectedAudioInputId } : undefined,
            echoCancellation: true,
            noiseSuppression: false,
            autoGainControl: false,
            channelCount: { ideal: 1 },
            sampleRate: { ideal: 48000 },
          },
        });
      } catch {
        terminalErrorRef.current = true;
        setStatus("error");
        statusRef.current = "error";
        setErrorMessage("Microphone access was denied. Allow access and try again.");
        return;
      }

      sourceStreamRef.current = stream;
      const activeDeviceId = stream.getAudioTracks()[0]?.getSettings().deviceId;
      void refreshAudioInputs(activeDeviceId);
      startInputMeter(stream);
    }

    const mimeType = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ].find((candidate) => MediaRecorder.isTypeSupported(candidate));

    const recorder = mimeType
      ? new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 128000 })
      : new MediaRecorder(stream);
    mediaRecorderRef.current = recorder;
    const recordedChunks: Blob[] = [];
    recordingHeardSpeechRef.current = false;
    recordingStartedAtRef.current = performance.now();
    lastSpeechAtRef.current = recordingStartedAtRef.current;
    discardRecordingRef.current = false;

    const Recognition = getSpeechRecognitionConstructor();
    if (Recognition) {
      const recognition = new Recognition();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = "en-LK";
      recognition.maxAlternatives = 3;
      recognition.onresult = (recognitionEvent) => {
        let words = "";
        for (let index = 0; index < recognitionEvent.results.length; index += 1) {
          words += `${recognitionEvent.results[index][0].transcript} `;
        }
        setLiveTranscript(words.trim());
      };
      // Browser live transcription is an enhancement. Whisper still receives
      // the recorded audio if the browser speech service is unavailable.
      recognition.onerror = () => undefined;
      recognition.onend = () => {
        if (mediaRecorderRef.current?.state === "recording") {
          try {
            recognition.start();
          } catch {
            // A restart can race with browser shutdown; the final Whisper pass remains available.
          }
        }
      };
      speechRecognitionRef.current = recognition;
      try {
        recognition.start();
      } catch {
        speechRecognitionRef.current = null;
      }
    }

    recorder.ondataavailable = (recordingEvent) => {
      if (recordingEvent.data.size > 0) recordedChunks.push(recordingEvent.data);
    };

    // MediaRecorder emits its final dataavailable event before onstop. Sending
    // the boundary here guarantees the server receives every audio chunk first.
    recorder.onstop = async () => {
      const shouldDiscard = discardRecordingRef.current;
      discardRecordingRef.current = false;
      mediaRecorderRef.current = null;

      if (!alwaysListeningRef.current) {
        stopInputMeter();
        stream.getTracks().forEach((track) => track.stop());
        sourceStreamRef.current = null;
      }

      if (shouldDiscard) {
        setStatus("idle");
        statusRef.current = "idle";
        return;
      }

      if (socket.readyState === WebSocket.OPEN) {
        const recording = new Blob(recordedChunks, { type: recorder.mimeType });
        socket.send(await recording.arrayBuffer());
        socket.send(JSON.stringify({ type: "end_utterance" }));
      } else {
        terminalErrorRef.current = true;
        setStatus("error");
        statusRef.current = "error";
        setErrorMessage("The connection closed before your recording was sent.");
      }
    };

    recorder.onerror = () => {
      stopInputMeter();
      speechRecognitionRef.current?.abort();
      speechRecognitionRef.current = null;
      stream.getTracks().forEach((track) => track.stop());
      sourceStreamRef.current = null;
      mediaRecorderRef.current = null;
      terminalErrorRef.current = true;
      setStatus("error");
      statusRef.current = "error";
      setErrorMessage("The browser could not record your microphone.");
    };

    // Buffer chunks in the browser and send one intact utterance on stop.
    recorder.start(1000);
    setStatus("recording");
    statusRef.current = "recording";
  }, [ensureSocket, refreshAudioInputs, selectedAudioInputId, startInputMeter, stopInputMeter]);

  const stopRecording = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
    const recognition = speechRecognitionRef.current;
    speechRecognitionRef.current = null;
    if (recognition) {
      recognition.onend = null;
      try {
        recognition.stop();
      } catch {
        recognition.abort();
      }
    }
    setStatus("thinking");
    statusRef.current = "thinking";
    recorder.stop();
  }, []);

  useEffect(() => {
    startRecordingRef.current = startRecording;
    stopRecordingRef.current = stopRecording;
  }, [startRecording, stopRecording]);

  const sendTextMessage = useCallback(
    async (text: string) => {
      const normalized = text.trim();
      if (!normalized) return;

      if (audioPlayingRef.current) {
        audioRef.current?.pause();
        audioCleanupRef.current?.();
      }

      setErrorMessage(null);
      setLiveTranscript("");
      setCurrentToolActivity(null);
      terminalErrorRef.current = false;

      try {
        const socket = await ensureSocket();
        setStatus("thinking");
        socket.send(JSON.stringify({ type: "text_message", text: normalized }));
      } catch {
        // ensureSocket provides the user-facing error.
      }
    },
    [ensureSocket]
  );

  const clearConversation = useCallback(() => {
    setMessages([]);
    setErrorMessage(null);
    setCurrentToolActivity(null);
    setLastToolName(null);
    setLiveTranscript("");
    setStatus((previous) => (previous === "error" ? "idle" : previous));
    terminalErrorRef.current = false;
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "reset_conversation" }));
    }
  }, []);

  const dismissError = useCallback(() => {
    setErrorMessage(null);
    terminalErrorRef.current = false;
    setStatus((previous) => (previous === "error" ? "idle" : previous));
  }, []);

  useEffect(() => {
    alwaysListeningRef.current = alwaysListening;

    if (alwaysListening && status === "idle" && !terminalErrorRef.current) {
      const timer = window.setTimeout(() => {
        if (!mediaRecorderRef.current && statusRef.current === "idle") {
          void startRecordingRef.current();
        }
      }, 250);
      return () => window.clearTimeout(timer);
    }

    if (!alwaysListening && mediaRecorderRef.current?.state === "recording") {
      discardRecordingRef.current = true;
      stopRecordingRef.current();
    }
  }, [alwaysListening, status]);

  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  useEffect(() => {
    void refreshAudioInputs();
    const handleDeviceChange = () => void refreshAudioInputs();
    navigator.mediaDevices?.addEventListener?.("devicechange", handleDeviceChange);
    return () => navigator.mediaDevices?.removeEventListener?.("devicechange", handleDeviceChange);
  }, [refreshAudioInputs]);

  useEffect(() => {
    return () => {
      mediaRecorderRef.current?.stream.getTracks().forEach((track) => track.stop());
      sourceStreamRef.current?.getTracks().forEach((track) => track.stop());
      stopInputMeter();
      speechRecognitionRef.current?.abort();
      audioRef.current?.pause();
      audioCleanupRef.current?.();
      socketRef.current?.close();
    };
  }, [stopInputMeter]);

  return {
    status,
    messages,
    currentToolActivity,
    lastToolName,
    liveTranscript,
    liveTranscriptionSupported,
    inputLevel,
    audioInputDevices,
    selectedAudioInputId,
    alwaysListening,
    errorMessage,
    setSelectedAudioInputId,
    setAlwaysListening,
    startRecording,
    stopRecording,
    sendTextMessage,
    clearConversation,
    dismissError,
  };
}
