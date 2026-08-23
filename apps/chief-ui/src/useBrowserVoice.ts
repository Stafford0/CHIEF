import { Dispatch, SetStateAction, useCallback, useEffect, useRef, useState } from "react";

interface SpeechRecognitionAlternativeLike {
  transcript: string;
}

interface SpeechRecognitionResultLike {
  readonly length: number;
  readonly isFinal: boolean;
  readonly [index: number]: SpeechRecognitionAlternativeLike;
}

interface SpeechRecognitionResultListLike {
  readonly length: number;
  readonly [index: number]: SpeechRecognitionResultLike;
}

interface SpeechRecognitionEventLike extends Event {
  readonly results: SpeechRecognitionResultListLike;
}

interface SpeechRecognitionErrorEventLike extends Event {
  readonly error: string;
}

interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  maxAlternatives: number;
  onstart: (() => void) | null;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

type VoiceWindow = Window & {
  SpeechRecognition?: SpeechRecognitionConstructor;
  webkitSpeechRecognition?: SpeechRecognitionConstructor;
};

export type VoiceStatus = "idle" | "listening" | "stopping" | "speaking" | "error";

export interface BrowserVoiceControls {
  inputAvailable: boolean;
  outputAvailable: boolean;
  listening: boolean;
  speaking: boolean;
  ttsEnabled: boolean;
  status: VoiceStatus;
  statusText: string;
  privacyText: string;
  toggleListening(currentDraft: string): void;
  stopListening(): void;
  toggleTts(): void;
  speak(text: string): void;
  stopAll(): void;
}

function recognitionErrorMessage(error: string): string {
  switch (error) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone or browser speech permission was not granted.";
    case "audio-capture":
      return "No usable microphone was found.";
    case "no-speech":
      return "No speech was detected. Push to talk when you are ready.";
    case "network":
      return "The browser speech service could not be reached.";
    default:
      return "Speech input stopped unexpectedly.";
  }
}

export function useBrowserVoice(
  setDraft: Dispatch<SetStateAction<string>>,
): BrowserVoiceControls {
  const voiceWindow = window as VoiceWindow;
  const recognitionConstructor =
    voiceWindow.SpeechRecognition ?? voiceWindow.webkitSpeechRecognition;
  const inputAvailable = Boolean(recognitionConstructor && window.isSecureContext);
  const outputAvailable = "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const draftPrefixRef = useRef("");
  const intentionalAbortRef = useRef(false);
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [ttsEnabled, setTtsEnabled] = useState(false);
  const [status, setStatus] = useState<VoiceStatus>("idle");
  const [statusText, setStatusText] = useState(() => {
    if (!recognitionConstructor) return "Speech input is unavailable in this browser.";
    if (!window.isSecureContext) return "Speech input needs HTTPS or localhost.";
    return "Push to talk is ready. Listening never starts automatically.";
  });

  useEffect(() => {
    if (!inputAvailable || !recognitionConstructor) return;

    const recognition = new recognitionConstructor();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = navigator.language || "en-US";
    recognition.maxAlternatives = 1;
    recognition.onstart = () => {
      setListening(true);
      setStatus("listening");
      setStatusText("Listening now. Push stop when you finish speaking.");
    };
    recognition.onresult = (event) => {
      let transcript = "";
      for (let index = 0; index < event.results.length; index += 1) {
        transcript += event.results[index][0]?.transcript ?? "";
      }
      const prefix = draftPrefixRef.current;
      const separator = prefix && transcript ? " " : "";
      setDraft(`${prefix}${separator}${transcript}`.trimStart());
    };
    recognition.onerror = (event) => {
      if (event.error === "aborted" && intentionalAbortRef.current) {
        intentionalAbortRef.current = false;
        setListening(false);
        setStatus("idle");
        setStatusText("Audio stopped. Nothing is listening or speaking.");
        return;
      }
      setListening(false);
      setStatus("error");
      setStatusText(recognitionErrorMessage(event.error));
    };
    recognition.onend = () => {
      setListening(false);
      setStatus((current) => (current === "error" ? current : "idle"));
      setStatusText((current) =>
        current.includes("permission") || current.includes("unexpectedly")
          ? current
          : "Speech input stopped. Review the text before transmitting.",
      );
    };
    recognitionRef.current = recognition;

    return () => {
      recognition.onstart = null;
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      recognition.abort();
      recognitionRef.current = null;
    };
  }, [inputAvailable, recognitionConstructor, setDraft]);

  const stopSpeaking = useCallback(() => {
    if (!outputAvailable) return;
    window.speechSynthesis.cancel();
    setSpeaking(false);
    setStatus((current) => (current === "speaking" ? "idle" : current));
  }, [outputAvailable]);

  const stopListening = useCallback(() => {
    if (!recognitionRef.current || !listening) return;
    setStatus("stopping");
    setStatusText("Finishing speech input…");
    recognitionRef.current.stop();
  }, [listening]);

  const toggleListening = useCallback(
    (currentDraft: string) => {
      if (listening) {
        stopListening();
        return;
      }
      if (!inputAvailable || !recognitionRef.current) {
        setStatus("error");
        setStatusText(
          recognitionConstructor
            ? "Speech input needs HTTPS or localhost."
            : "Speech input is unavailable in this browser.",
        );
        return;
      }

      stopSpeaking();
      intentionalAbortRef.current = false;
      draftPrefixRef.current = currentDraft.trim();
      setStatusText("Requesting microphone access for this turn only…");
      try {
        recognitionRef.current.start();
      } catch {
        setStatus("error");
        setStatusText("Speech input is already starting. Try again in a moment.");
      }
    },
    [inputAvailable, listening, recognitionConstructor, stopListening, stopSpeaking],
  );

  const toggleTts = useCallback(() => {
    if (!outputAvailable) {
      setStatus("error");
      setStatusText("Spoken replies are unavailable in this browser.");
      return;
    }
    setTtsEnabled((enabled) => {
      if (enabled) {
        window.speechSynthesis.cancel();
        setSpeaking(false);
        setStatus("idle");
        setStatusText("Spoken replies are off.");
        return false;
      }
      setStatusText("Spoken replies are on. Browser or OS voice processing may vary.");
      return true;
    });
  }, [outputAvailable]);

  const speak = useCallback(
    (text: string) => {
      if (!ttsEnabled || !outputAvailable || !text.trim()) return;
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = navigator.language || "en-US";
      utterance.onstart = () => {
        setSpeaking(true);
        setStatus("speaking");
        setStatusText("Speaking. Push stop audio to interrupt.");
      };
      utterance.onend = () => {
        setSpeaking(false);
        setStatus("idle");
        setStatusText("Spoken reply finished.");
      };
      utterance.onerror = () => {
        setSpeaking(false);
        setStatus("error");
        setStatusText("The browser could not play this spoken reply.");
      };
      window.speechSynthesis.speak(utterance);
    },
    [outputAvailable, ttsEnabled],
  );

  const stopAll = useCallback(() => {
    if (recognitionRef.current && listening) {
      intentionalAbortRef.current = true;
      recognitionRef.current.abort();
    }
    stopSpeaking();
    setListening(false);
    setStatus("idle");
    setStatusText("Audio stopped. Nothing is listening or speaking.");
  }, [listening, stopSpeaking]);

  useEffect(
    () => () => {
      recognitionRef.current?.abort();
      if (outputAvailable) window.speechSynthesis.cancel();
    },
    [outputAvailable],
  );

  const privacyText = inputAvailable
    ? "Audio is handled by the browser speech service and is not recorded or retained by CHIEF. Browser processing may be local or cloud-based."
    : "CHIEF does not request microphone access unless you push to talk. Camera access remains disabled.";

  return {
    inputAvailable,
    outputAvailable,
    listening,
    speaking,
    ttsEnabled,
    status,
    statusText,
    privacyText,
    toggleListening,
    stopListening,
    toggleTts,
    speak,
    stopAll,
  };
}
