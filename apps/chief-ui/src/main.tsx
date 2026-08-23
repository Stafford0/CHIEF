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
  Laptop,
  LockKeyhole,
  MemoryStick,
  MessageSquare,
  Network,
  Radio,
  RefreshCw,
  Send,
  Server,
  Settings,
  ShieldCheck,
  Smartphone,
  TerminalSquare,
  Wrench,
  Zap,
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
type ChatResponse = {
  response: string;
  provider: string;
  model: string;
  session_id: string;
};
type Dashboard = {
  captured_at: string;
  host: {
    hostname: string;
    os: string;
    os_release: string;
    architecture: string;
    python: string;
    cpu_count: number | null;
  };
  cpu: { percent: number | null };
  memory: { total_gb: number | null; used_gb: number | null; percent: number | null };
  disk: { total_gb: number; used_gb: number; free_gb: number; percent: number };
  gpu: {
    available: boolean;
    name?: string;
    utilization_percent?: number;
    memory_used_mb?: number;
    memory_total_mb?: number;
    temperature_c?: number;
  };
  network: {
    hostname: string;
    addresses: string[];
    adapters: Array<{ name: string; description: string; link_speed: string }>;
  };
  ollama: { online: boolean; models: string[] };
  runtime: {
    api_status: string;
    active_model: string;
    model_provider: string;
    sessions: number;
    tools: Array<{
      name: string;
      description: string;
      risk: string;
      requires_approval: boolean;
    }>;
    permissions: { approval_gated: number; automatic: number };
    agents: Array<{ name: string; status: string; kind: string }>;
    queued_tasks: Array<{ name?: string; status?: string }>;
    recent_executions: Array<{ name?: string; status?: string }>;
    projects: Array<{ name: string; status: string; path: string }>;
    objectives: Array<{ name: string; status: string }>;
  };
};

type View = "overview" | "chat";

const API_BASE =
  import.meta.env.VITE_CHIEF_API_URL || `http://${window.location.hostname}:8000`;

const navItems = [
  [Activity, "Status", "overview"],
  [MessageSquare, "Chat", "chat"],
  [BrainCircuit, "Memory", "overview"],
  [Wrench, "Tools", "overview"],
  [Database, "Projects", "overview"],
  [Radio, "Sessions", "overview"],
  [ShieldCheck, "Permissions", "overview"],
  [Settings, "System", "overview"],
] as const;

function clamp(value: number | null | undefined, fallback = 0) {
  if (value == null || Number.isNaN(value)) return fallback;
  return Math.max(0, Math.min(100, value));
}

function fmtPct(value: number | null | undefined) {
  return value == null ? "--" : `${Math.round(value)}%`;
}

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [view, setView] = useState<View>("overview");
  const [clock, setClock] = useState(new Date());
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: "assistant", content: "CHIEF command interface online. Awaiting directive." },
  ]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  async function loadTelemetry() {
    try {
      const [healthRes, systemRes, dashboardRes] = await Promise.all([
        fetch(`${API_BASE}/health`),
        fetch(`${API_BASE}/system`),
        fetch(`${API_BASE}/dashboard`),
      ]);
      if (!healthRes.ok || !systemRes.ok || !dashboardRes.ok) {
        throw new Error("CHIEF telemetry unavailable");
      }
      setHealth(await healthRes.json());
      setSystem(await systemRes.json());
      setDashboard(await dashboardRes.json());
      setApiError(null);
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "Connection failed");
    }
  }

  useEffect(() => {
    loadTelemetry();
    const telemetryTimer = window.setInterval(loadTelemetry, 5000);
    const clockTimer = window.setInterval(() => setClock(new Date()), 1000);
    return () => {
      window.clearInterval(telemetryTimer);
      window.clearInterval(clockTimer);
    };
  }, []);

  const online = health?.status === "online" && dashboard?.runtime.api_status === "online";
  const activeModel = dashboard?.runtime.active_model || "qwen3:4b";
  const toolCount = dashboard?.runtime.tools.length || 0;
  const approvalCount = dashboard?.runtime.permissions.approval_gated || 0;
  const autoCount = dashboard?.runtime.permissions.automatic || 0;
  const securityPct = toolCount ? Math.round((approvalCount / toolCount) * 100) : 0;
  const overallHealth = useMemo(() => {
    if (!online) return 0;
    const metrics = [
      dashboard?.cpu.percent,
      dashboard?.memory.percent,
      dashboard?.disk.percent,
      dashboard?.gpu.available ? dashboard.gpu.utilization_percent : null,
    ].filter((value): value is number => typeof value === "number");
    if (!metrics.length) return 100;
    const worstLoad = Math.max(...metrics);
    return Math.max(1, Math.round(100 - worstLoad * 0.35));
  }, [dashboard, online]);

  const clientIsPhone = /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent);

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
      loadTelemetry();
    } catch (error) {
      const text = error instanceof Error ? error.message : "Chat failed";
      setApiError(text);
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
      <header className="topbar hud-frame">
        <div className="brand-block">
          <div className="brand-mark"><Bot size={26} /></div>
          <div>
            <h1>CHIEF</h1>
            <p>Cognitive Hub for Intelligence, Execution &amp; Foresight</p>
          </div>
        </div>
        <div className="mission-strip">
          <HeaderCell label="SYSTEM STATUS" value={online ? "ACTIVE" : "OFFLINE"} hot={online} />
          <HeaderCell label="OPERATION" value={system?.milestone || "CHIEF ZERO"} sub={system?.environment || "development"} />
          <HeaderCell label="SYSTEM TIME" value={clock.toLocaleTimeString([], { hour12: false })} sub={clock.toLocaleDateString()} />
          <HeaderCell label="OPERATOR" value="DIRECTOR" />
          <HeaderCell label="SESSION ID" value={sessionId ? sessionId.slice(0, 8).toUpperCase() : "UNASSIGNED"} />
        </div>
      </header>

      <section className="workspace">
        <aside className="left-column">
          <Panel title="COMMAND" className="nav-panel">
            <nav>
              {navItems.map(([Icon, label, target]) => {
                const active = (view === "chat" && label === "Chat") || (view === "overview" && label === "Status");
                return (
                  <button key={label} className={`nav-item ${active ? "active" : ""}`} onClick={() => setView(target)}>
                    <Icon size={15} />
                    <span>{label}</span>
                    {active && <ChevronRight size={13} className="push" />}
                  </button>
                );
              })}
            </nav>
          </Panel>

          <Panel title="CHIEF OBJECTIVES" className="compact-panel">
            {(dashboard?.runtime.objectives || []).map((objective, index) => (
              <Objective key={objective.name} name={objective.name} status={objective.status} primary={index === 0} />
            ))}
            {!dashboard && <Empty text="Awaiting core telemetry" />}
          </Panel>

          <Panel title="SECURITY POSTURE" className="compact-panel warning-panel">
            <div className="risk-head"><span>APPROVAL GATED</span><strong>{securityPct}%</strong></div>
            <div className="risk-bar"><span style={{ width: `${securityPct}%` }} /></div>
            <InfoRow label="GUARDED TOOLS" value={String(approvalCount)} />
            <InfoRow label="AUTOMATIC" value={String(autoCount)} />
          </Panel>

          <Panel title="COMMS CHANNEL" className="compact-panel comms-panel">
            <div className={`waveform ${online ? "live" : ""}`} aria-hidden="true">
              {Array.from({ length: 34 }, (_, i) => <i key={i} style={{ height: `${18 + ((i * 23) % 52)}%` }} />)}
            </div>
            <div className="channel-line"><span>API</span><strong>{online ? "ENCRYPTED / LIVE" : "NO LINK"}</strong></div>
            <div className="channel-line"><span>HOST</span><strong>{dashboard?.network.hostname || "--"}</strong></div>
          </Panel>
        </aside>

        <section className="center-column">
          {view === "overview" ? (
            <>
              <section className="map-panel hud-frame">
                <div className="map-titlebar">
                  <div><span className="eyebrow">CHIEF SYSTEMS GRID</span><h2>LOCAL OPERATIONS NETWORK</h2></div>
                  <div className="live-badge"><i /> LIVE <b>{dashboard?.network.addresses.length || 0}</b></div>
                </div>
                <NetworkMap dashboard={dashboard} online={online} clientIsPhone={clientIsPhone} />
                <div className="map-toolbar">
                  <span><Network size={14} /> TOPOLOGY</span>
                  <span><Server size={14} /> SERVICES</span>
                  <span><BrainCircuit size={14} /> AGENTS</span>
                  <span className="scan"><i /> SCANNING <b>{online ? "ACTIVE" : "PAUSED"}</b></span>
                </div>
              </section>

              <section className="bottom-grid">
                <Panel title="TASK QUEUE">
                  {(dashboard?.runtime.queued_tasks.length || 0) === 0 ? <Empty text="No queued tasks" /> :
                    dashboard?.runtime.queued_tasks.map((task, i) => <Log key={i} time={`Q${i + 1}`} text={task.name || "Task"} />)}
                  <InfoRow label="SESSIONS" value={String(dashboard?.runtime.sessions ?? 0)} />
                  <InfoRow label="PROJECTS" value={String(dashboard?.runtime.projects.length ?? 0)} />
                </Panel>
                <Panel title="COMMAND LOG">
                  <Log time="API" text={online ? "FastAPI core responding" : "Core connection unavailable"} warn={!online} />
                  <Log time="AI" text={`${dashboard?.runtime.model_provider || "ollama"} / ${activeModel}`} />
                  <Log time="NET" text={dashboard?.network.addresses[0] || API_BASE} />
                  <Log time="EXEC" text={(dashboard?.runtime.recent_executions.length || 0) ? "Execution telemetry available" : "No persistent execution log yet"} />
                </Panel>
                <Panel title="QUICK ACTIONS">
                  <div className="action-grid">
                    <Action icon={<MessageSquare size={15} />} label="Open Chat" onClick={() => setView("chat")} />
                    <Action icon={<RefreshCw size={15} />} label="Refresh" onClick={loadTelemetry} />
                    <Action icon={<Wrench size={15} />} label={`${toolCount} Tools`} />
                    <Action icon={<ShieldCheck size={15} />} label="Permissions" />
                  </div>
                </Panel>
              </section>
            </>
          ) : (
            <section className="chat-panel hud-frame">
              <div className="map-titlebar">
                <div><span className="eyebrow">DIRECT CHANNEL</span><h2>CHIEF COMMAND INTERFACE</h2></div>
                <div className="live-badge"><i /> {busy ? "PROCESSING" : "READY"}</div>
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
                <TerminalSquare size={17} />
                <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Issue directive to CHIEF..." aria-label="Message CHIEF" />
                <button type="submit" disabled={busy}><Send size={15} /> TRANSMIT</button>
              </form>
            </section>
          )}
        </section>

        <aside className="right-column">
          <Panel title="SYSTEM HEALTH" className="health-panel">
            <div className={`health-ring ${online ? "online" : "offline"}`}>
              <div><strong>{online ? `${overallHealth}%` : "--"}</strong><span>{online ? "OPTIMAL" : "NO LINK"}</span></div>
            </div>
            <Metric label="CPU" value={fmtPct(dashboard?.cpu.percent)} pct={clamp(dashboard?.cpu.percent)} />
            <Metric label="MEMORY" value={fmtPct(dashboard?.memory.percent)} pct={clamp(dashboard?.memory.percent)} />
            <Metric label="DISK" value={fmtPct(dashboard?.disk.percent)} pct={clamp(dashboard?.disk.percent)} />
            <Metric label="GPU" value={dashboard?.gpu.available ? fmtPct(dashboard.gpu.utilization_percent) : "N/A"} pct={clamp(dashboard?.gpu.utilization_percent)} />
          </Panel>

          <Panel title="MODEL INTELLIGENCE">
            <div className="model-primary"><BrainCircuit size={28} /><div><span>ACTIVE MODEL</span><strong>{activeModel}</strong></div></div>
            <div className="model-list">
              {(dashboard?.ollama.models || []).slice(0, 4).map((model) => <div key={model}><i className={model === activeModel ? "active-dot" : "idle-dot"} />{model}</div>)}
              {!dashboard?.ollama.models.length && <Empty text="Ollama models unavailable" />}
            </div>
          </Panel>

          <Panel title="AGENT TELEMETRY">
            {(dashboard?.runtime.agents || []).map((agent, index) => (
              <div className="agent-row" key={agent.name}>
                <div><strong>A{index + 1} · {agent.name}</strong><span>{agent.kind}</span></div>
                <b className={agent.status === "operational" ? "good" : "bad"}>{agent.status}</b>
                <Spark seed={index + 2} />
              </div>
            ))}
          </Panel>

          <Panel title="SYSTEM DIAGNOSTICS" className="diagnostics-panel">
            <div className="diag-grid">
              <div>
                <InfoRow label="API" value={online ? "OPERATIONAL" : "OFFLINE"} />
                <InfoRow label="HOST" value={dashboard?.host.hostname || "--"} />
                <InfoRow label="OS" value={`${dashboard?.host.os || "--"} ${dashboard?.host.os_release || ""}`} />
                <InfoRow label="CORES" value={String(dashboard?.host.cpu_count ?? "--")} />
                <InfoRow label="TOOLS" value={String(toolCount)} />
                <InfoRow label="SESSIONS" value={String(dashboard?.runtime.sessions ?? 0)} />
              </div>
              <div>
                <GaugeBlock icon={<Cpu size={14} />} label="CPU" value={fmtPct(dashboard?.cpu.percent)} />
                <GaugeBlock icon={<MemoryStick size={14} />} label="RAM" value={dashboard?.memory.total_gb ? `${dashboard.memory.used_gb}/${dashboard.memory.total_gb} GB` : "--"} />
                <GaugeBlock icon={<HardDrive size={14} />} label="DISK FREE" value={dashboard ? `${dashboard.disk.free_gb} GB` : "--"} />
                <GaugeBlock icon={<Zap size={14} />} label="GPU TEMP" value={dashboard?.gpu.temperature_c != null ? `${dashboard.gpu.temperature_c}°C` : "--"} />
              </div>
            </div>
          </Panel>

          <Panel title="ALERTS FEED" className="alerts-panel">
            {apiError ? <Alert text={apiError} /> : null}
            {!dashboard?.ollama.online && <Alert text="Ollama service is offline" />}
            {dashboard?.gpu.available === false && <Alert text="GPU telemetry unavailable" mild />}
            {!apiError && dashboard?.ollama.online && dashboard?.gpu.available !== false && <div className="clear"><ShieldCheck size={15} /> No active system alerts</div>}
          </Panel>
        </aside>
      </section>

      <footer className="footer-bar">
        <span><i className={online ? "online-dot" : "offline-dot"} /> LINK: {online ? "SECURE" : "DISCONNECTED"}</span>
        <span>NODE: {dashboard?.host.hostname || "CHIEF-LOCAL"}</span>
        <span>MODEL: {activeModel}</span>
        <span>DATA INTEGRITY: {dashboard ? "VERIFIED" : "PENDING"}</span>
        <span>CLASSIFICATION: LOCAL CONTROL</span>
        <span>CLEARANCE: DIRECTOR</span>
      </footer>
    </main>
  );
}

function NetworkMap({ dashboard, online, clientIsPhone }: { dashboard: Dashboard | null; online: boolean; clientIsPhone: boolean }) {
  const host = dashboard?.host.hostname || "CHIEF HOST";
  const model = dashboard?.runtime.active_model || "LOCAL MODEL";
  const ip = dashboard?.network.addresses[0] || "127.0.0.1";
  const toolCount = dashboard?.runtime.tools.length || 0;
  const sessions = dashboard?.runtime.sessions || 0;
  const clientLabel = clientIsPhone ? "PHONE CLIENT" : "DESKTOP CLIENT";
  const nodes = [
    { x: 50, y: 50, label: "CHIEF CORE", sub: online ? "ONLINE" : "OFFLINE", tone: "core" },
    { x: 50, y: 15, label: host.toUpperCase(), sub: ip, tone: "host" },
    { x: 80, y: 30, label: "OLLAMA", sub: dashboard?.ollama.online ? "SERVICE LIVE" : "OFFLINE", tone: "service" },
    { x: 82, y: 68, label: model.toUpperCase(), sub: "ACTIVE MODEL", tone: "agent" },
    { x: 50, y: 84, label: "TOOL REGISTRY", sub: `${toolCount} TOOLS`, tone: "service" },
    { x: 18, y: 69, label: "MEMORY", sub: "LOCAL STORE", tone: "agent" },
    { x: 17, y: 31, label: clientLabel, sub: "OPERATOR LINK", tone: "client" },
    { x: 33, y: 17, label: "FASTAPI", sub: "PORT 8000", tone: "service" },
    { x: 67, y: 83, label: "SESSIONS", sub: `${sessions} ACTIVE`, tone: "client" },
  ];
  const center = nodes[0];

  return (
    <div className="network-map">
      <div className="grid-floor" />
      <div className="radar-ring r1" /><div className="radar-ring r2" /><div className="radar-ring r3" /><div className="radar-ring r4" />
      <div className="crosshair horizontal" /><div className="crosshair vertical" />
      <span className="compass north">N</span><span className="compass east">E</span><span className="compass south">S</span><span className="compass west">W</span>
      <svg className="connection-layer" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        {nodes.slice(1).map((node, i) => (
          <g key={node.label}>
            <line x1={center.x} y1={center.y} x2={node.x} y2={node.y} className={i % 3 === 1 ? "connection hot" : "connection"} />
            <circle cx={node.x} cy={node.y} r=".65" className="pulse-point" />
          </g>
        ))}
      </svg>
      {nodes.map((node, i) => (
        <div key={node.label} className={`map-node ${node.tone} ${i === 0 ? "center-node" : ""}`} style={{ left: `${node.x}%`, top: `${node.y}%` }}>
          <div className="node-icon">{i === 0 ? <Bot size={17} /> : i === 1 ? <Laptop size={14} /> : i === 6 && clientIsPhone ? <Smartphone size={14} /> : i === 6 ? <Laptop size={14} /> : i === 3 || i === 5 ? <BrainCircuit size={14} /> : <Server size={14} />}</div>
          <div className="node-label"><strong>{node.label}</strong><span>{node.sub}</span></div>
        </div>
      ))}
      <div className="map-readout left-readout"><span>HOST ADDRESS</span><strong>{ip}</strong><small>{dashboard?.network.adapters[0]?.link_speed || "LOCAL NETWORK"}</small></div>
      <div className="map-readout right-readout"><span>MODEL SERVICE</span><strong>{dashboard?.ollama.online ? "OLLAMA ONLINE" : "OLLAMA OFFLINE"}</strong><small>{dashboard?.ollama.models.length || 0} MODELS INSTALLED</small></div>
    </div>
  );
}

function HeaderCell({ label, value, sub, hot = false }: { label: string; value: string; sub?: string; hot?: boolean }) {
  return <div className="header-cell"><span>{label}</span><strong className={hot ? "teal" : ""}>{value}</strong>{sub && <small>{sub}</small>}</div>;
}
function Panel({ title, children, className = "" }: { title: string; children: React.ReactNode; className?: string }) {
  return <section className={`panel hud-frame ${className}`}><div className="panel-title"><span>{title}</span><i /></div>{children}</section>;
}
function Objective({ name, status, primary }: { name: string; status: string; primary?: boolean }) {
  return <div className={`objective ${primary ? "primary" : ""}`}><span>{primary ? "PRIMARY" : "SECONDARY"}</span><strong>{name}</strong><small><i className="check" /> {status.toUpperCase()}</small></div>;
}
function Metric({ label, value, pct }: { label: string; value: string; pct: number }) {
  return <div className="metric"><div><span>{label}</span><strong>{value}</strong></div><div className="metric-track"><span style={{ width: `${pct}%` }} /></div></div>;
}
function InfoRow({ label, value }: { label: string; value: string }) {
  return <div className="info-row"><span>{label}</span><strong>{value}</strong></div>;
}
function Log({ time, text, warn = false }: { time: string; text: string; warn?: boolean }) {
  return <div className="log-row"><code>[{time}]</code><span className={warn ? "warn" : ""}>{text}</span></div>;
}
function Action({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick?: () => void }) {
  return <button className="action-button" onClick={onClick}>{icon}<span>{label}</span></button>;
}
function GaugeBlock({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return <div className="gauge-block"><span>{icon}{label}</span><strong>{value}</strong></div>;
}
function Spark({ seed }: { seed: number }) {
  return <div className="spark" aria-hidden="true">{Array.from({ length: 15 }, (_, i) => <i key={i} style={{ height: `${10 + ((i * 19 + seed * 13) % 75)}%` }} />)}</div>;
}
function Alert({ text, mild = false }: { text: string; mild?: boolean }) {
  return <div className={`alert ${mild ? "mild" : ""}`}><AlertTriangle size={14} /><span>{text}</span></div>;
}
function Empty({ text }: { text: string }) {
  return <div className="empty-state">{text}</div>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
