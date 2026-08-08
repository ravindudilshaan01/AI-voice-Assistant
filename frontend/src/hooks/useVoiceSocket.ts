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

interface UseVoiceSocketResult {
  status: AssistantStatus;
  messages: Message[];
  currentToolActivity: string | null;
  lastToolName: string | null;
  errorMessage: string | null;
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
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioPlayingRef = useRef(false);
  const terminalErrorRef = useRef(false);

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
      const socket = new WebSocket(WS_URL);
      socket.binaryType = "arraybuffer";
      socketRef.current = socket;

      socket.onopen = () => resolve(socket);

      socket.onerror = () => {
        terminalErrorRef.current = true;
        setStatus("error");
        setErrorMessage("Could not connect to the assistant server.");
        if (socketRef.current === socket) socketRef.current = null;
        reject(new Error("WebSocket connection failed"));
      };

      socket.onclose = () => {
        if (socketRef.current === socket) socketRef.current = null;
        setCurrentToolActivity(null);
        if (!terminalErrorRef.current) setStatus("idle");
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
              setCurrentToolActivity(null);
              setLastToolName(null);
              setStatus("thinking");
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
              setErrorMessage(String(payload.message ?? "Something went wrong."));
              break;

            case "done":
              setCurrentToolActivity(null);
              if (!audioPlayingRef.current && !terminalErrorRef.current) {
                setStatus("idle");
              }
              break;
          }
          return;
        }

        audioRef.current?.pause();
        audioPlayingRef.current = true;
        setStatus("speaking");

        const blob = new Blob([event.data], { type: "audio/mpeg" });
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audioRef.current = audio;

        const cleanup = () => {
          if (audioRef.current === audio) audioRef.current = null;
          audioPlayingRef.current = false;
          URL.revokeObjectURL(url);
          if (!terminalErrorRef.current) setStatus("idle");
        };

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
    setErrorMessage(null);
    terminalErrorRef.current = false;

    let socket: WebSocket;
    try {
      socket = await ensureSocket();
    } catch {
      return;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch {
      terminalErrorRef.current = true;
      setStatus("error");
      setErrorMessage("Microphone access was denied. Allow access and try again.");
      return;
    }

    const mimeType = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ].find((candidate) => MediaRecorder.isTypeSupported(candidate));

    const recorder = mimeType
      ? new MediaRecorder(stream, { mimeType })
      : new MediaRecorder(stream);
    mediaRecorderRef.current = recorder;

    recorder.ondataavailable = (recordingEvent) => {
      if (recordingEvent.data.size > 0 && socket.readyState === WebSocket.OPEN) {
        socket.send(recordingEvent.data);
      }
    };

    // MediaRecorder emits its final dataavailable event before onstop. Sending
    // the boundary here guarantees the server receives every audio chunk first.
    recorder.onstop = () => {
      stream.getTracks().forEach((track) => track.stop());
      mediaRecorderRef.current = null;
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "end_utterance" }));
      } else {
        terminalErrorRef.current = true;
        setStatus("error");
        setErrorMessage("The connection closed before your recording was sent.");
      }
    };

    recorder.onerror = () => {
      stream.getTracks().forEach((track) => track.stop());
      mediaRecorderRef.current = null;
      terminalErrorRef.current = true;
      setStatus("error");
      setErrorMessage("The browser could not record your microphone.");
    };

    recorder.start(250);
    setStatus("recording");
  }, [ensureSocket]);

  const stopRecording = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
    setStatus("thinking");
    recorder.stop();
  }, []);

  const sendTextMessage = useCallback(
    async (text: string) => {
      const normalized = text.trim();
      if (!normalized) return;

      setErrorMessage(null);
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
    return () => {
      mediaRecorderRef.current?.stream.getTracks().forEach((track) => track.stop());
      audioRef.current?.pause();
      socketRef.current?.close();
    };
  }, []);

  return {
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
  };
}
