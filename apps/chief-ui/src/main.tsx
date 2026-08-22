import React, { FormEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  AlertTriangle,
  Bot,
  BrainCircuit,
  ChevronRight,
  Cpu,
  Database,
  Gauge,
  HardDrive,
  MessageSquare,
  Radio,
  Send,
  Settings,
  ShieldCheck,
  TerminalSquare,
  Wrench,
} from "lucide-react";
import "./styles.css";

type Health = { status: string; system: string; version: string };
type SystemInfo = {
  name: string;
  full_name: string;
  version: string;
  milestone: string;
  environment: string;
};
type ChatMessage = { role: "user" | "assistant"; content: string };
type ChatResponse = { response: string; provider: string; model: string; session_id: string };

const API_BASE =
  import.meta.env.VITE_CHIEF_API_URL || `http://${window.location.hostname}:8000`;

const navItems = [
  [Activity, "Overview"],
  [MessageSquare, "Chat"],
  [BrainCircuit, "Memory"],
  [Wrench, "Tools"],
  [Database, "Projects"],
  [Radio, "Sessions"],
  [ShieldCheck, "Permissions"],
  [Settings, "Settings"],
] as const;

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: "assistant", content: "CHIEF command interface online. Awaiting directive." },
  ]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [healthRes, systemRes] = await Promise.all([
          fetch(`${API_BASE}/health`),
          fetch(`${API_BASE}/system`),
        ]);
        if (!healthRes.ok || !systemRes.ok) throw new Error("CHIEF API unavailable");
        setHealth(await healthRes.json());
        setSystem(await systemRes.json());
        setApiError(null);
      } catch (error) {
        setApiError(error instanceof Error ? error.message : "Connection failed");
      }
    };
    load();
    const timer = window.setInterval(load, 10000);
    return () => window.clearInterval(timer);
  }, []);

  const online = health?.status === "online";
  const modelLabel = useMemo(() => {
    const last = [...messages].reverse().find((m) => m.role === "assistant");
    return last ? "LOCAL AI READY" : "STANDBY";
  }, [messages]);

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || busy) return;

    setMessages((current) => [...current, { role: "user", content: message }]);
    setInput("");
    setBusy(true);

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: sessionId }),
      });
      if (!res.ok) throw new Error(`Chat request failed (${res.status})`);
      const data: ChatResponse = await res.json();
      setSessionId(data.session_id);
      setMessages((current) => [...current, { role: "assistant", content: data.response }]);
      setApiError(null);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "Chat failed");
      setMessages((current) => [
        ...current,
        { role: "assistant", content: "Unable to reach CHIEF core. Check the API connection." },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell">
      <header className="topbar panel-edge">
        <div className="brand-block">
          <div className="brand-mark"><Bot size={28} /></div>
          <div>
            <h1>CHIEF</h1>
            <p>Cognitive Hub for Intelligence, Execution &amp; Foresight</p>
          </div>
        </div>
        <div className="header-status">
          <StatusChip label="CORE" value={online ? "ONLINE" : "OFFLINE"} active={online} />
          <StatusChip label="MILESTONE" value={system?.milestone || "CHIEF ZERO"} />
          <StatusChip label="MODE" value={system?.environment?.toUpperCase() || "DEVELOPMENT"} />
        </div>
      </header>

      <section className="workspace">
        <aside className="left-rail panel-edge">
          <div className="rail-title">COMMAND</div>
          <nav>
            {navItems.map(([Icon, label], index) => (
              <button key={label} className={`nav-item ${index === 1 ? "active" : ""}`}>
                <Icon size={17} />
                <span>{label}</span>
                {index === 1 && <ChevronRight size={14} className="push" />}
              </button>
            ))}
          </nav>

          <div className="objective-card">
            <span className="eyebrow">ACTIVE OBJECTIVE</span>
            <strong>CHIEF UI-001</strong>
            <p>Command Center Shell</p>
            <div className="progress-track"><span style={{ width: "42%" }} /></div>
            <small>Interface deployment in progress</small>
          </div>
        </aside>

        <section className="center-stack">
          <section className="command-panel panel-edge">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">COMMAND CENTER</span>
                <h2>DIRECT INTERFACE</h2>
              </div>
              <div className="signal"><span /> {modelLabel}</div>
            </div>

            <div className="chat-stream">
              {messages.map((message, index) => (
                <article key={index} className={`message ${message.role}`}>
                  <div className="message-tag">{message.role === "assistant" ? "CHIEF" : "OPERATOR"}</div>
                  <p>{message.content}</p>
                </article>
              ))}
              {busy && <article className="message assistant"><div className="message-tag">CHIEF</div><p className="typing">PROCESSING DIRECTIVE...</p></article>}
            </div>

            <form className="command-input" onSubmit={sendMessage}>
              <TerminalSquare size={18} />
              <input
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder="Issue directive to CHIEF..."
                aria-label="Message CHIEF"
              />
              <button type="submit" disabled={busy}><Send size={17} /> SEND</button>
            </form>
          </section>

          <section className="lower-grid">
            <Panel title="ACTIVE OBJECTIVES">
              <Objective title="CHIEF interface" detail="Build tactical command shell" status="ACTIVE" />
              <Objective title="Parcel Signals" detail="Project intelligence integration" status="READY" />
              <Objective title="Local control" detail="Phone-accessible operations" status="READY" />
            </Panel>
            <Panel title="EXECUTION LOG">
              <Log time="NOW" text="Command interface initialized" />
              <Log time="API" text={online ? "FastAPI core responding" : "Core connection unavailable"} warn={!online} />
              <Log time="NET" text={`Endpoint ${API_BASE}`} />
            </Panel>
            <Panel title="QUICK ACTIONS">
              <div className="action-grid">
                <Action icon={<TerminalSquare size={17} />} label="New Task" />
                <Action icon={<Gauge size={17} />} label="System Scan" />
                <Action icon={<Database size={17} />} label="Projects" />
                <Action icon={<ShieldCheck size={17} />} label="Permissions" />
              </div>
            </Panel>
          </section>
        </section>

        <aside className="right-rail">
          <Panel title="SYSTEM HEALTH">
            <div className={`health-ring ${online ? "online" : "offline"}`}>
              <div><strong>{online ? "100%" : "--"}</strong><span>{online ? "NOMINAL" : "NO LINK"}</span></div>
            </div>
            <Metric label="CORE" value={online ? "ONLINE" : "OFFLINE"} pct={online ? 100 : 8} />
            <Metric label="API" value={health?.version || "--"} pct={online ? 92 : 8} />
            <Metric label="SESSION" value={sessionId ? "ACTIVE" : "READY"} pct={sessionId ? 84 : 58} />
          </Panel>

          <Panel title="LOCAL MODEL">
            <div className="model-status">
              <BrainCircuit size={35} />
              <div><span>ENGINE</span><strong>OLLAMA</strong></div>
            </div>
            <p className="muted">Connected through CHIEF Core. Live model identity is returned with chat responses.</p>
          </Panel>

          <Panel title="ALERTS">
            {apiError ? (
              <div className="alert"><AlertTriangle size={17} /><span>{apiError}</span></div>
            ) : (
              <div className="clear"><ShieldCheck size={17} /> No active system alerts</div>
            )}
          </Panel>

          <Panel title="SYSTEM IDENTITY">
            <InfoRow label="NAME" value={system?.name || "CHIEF"} />
            <InfoRow label="VERSION" value={system?.version || "0.0.1"} />
            <InfoRow label="ENV" value={system?.environment || "development"} />
          </Panel>
        </aside>
      </section>

      <footer>
        <span><i className={online ? "online-dot" : "offline-dot"} /> LINK: {online ? "SECURE" : "DISCONNECTED"}</span>
        <span>NODE: CHIEF-LOCAL</span>
        <span>SESSION: {sessionId ? sessionId.slice(0, 8).toUpperCase() : "UNASSIGNED"}</span>
      </footer>
    </main>
  );
}

function StatusChip({ label, value, active = false }: { label: string; value: string; active?: boolean }) {
  return <div className="status-chip"><span>{label}</span><strong className={active ? "teal" : ""}>{value}</strong></div>;
}
function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="panel panel-edge"><div className="panel-title">{title}</div>{children}</section>;
}
function Objective({ title, detail, status }: { title: string; detail: string; status: string }) {
  return <div className="objective-row"><div><strong>{title}</strong><span>{detail}</span></div><b>{status}</b></div>;
}
function Log({ time, text, warn = false }: { time: string; text: string; warn?: boolean }) {
  return <div className="log-row"><code>{time}</code><span className={warn ? "warn" : ""}>{text}</span></div>;
}
function Action({ icon, label }: { icon: React.ReactNode; label: string }) {
  return <button className="action-button">{icon}<span>{label}</span></button>;
}
function Metric({ label, value, pct }: { label: string; value: string; pct: number }) {
  return <div className="metric"><div><span>{label}</span><strong>{value}</strong></div><div className="metric-track"><span style={{ width: `${pct}%` }} /></div></div>;
}
function InfoRow({ label, value }: { label: string; value: string }) {
  return <div className="info-row"><span>{label}</span><strong>{value}</strong></div>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
