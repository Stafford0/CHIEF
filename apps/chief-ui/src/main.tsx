import React, { FormEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity, AlertTriangle, Bot, BrainCircuit, ChevronRight, Cpu, Database, Gauge,
  HardDrive, Laptop, LockKeyhole, MemoryStick, MessageSquare, Network, Radio,
  RefreshCw, Send, Server, Settings, ShieldCheck, Smartphone, TerminalSquare,
  Wrench, Zap,
} from "lucide-react";
import "./styles.css";

type Health = { status: string; system: string; version: string };
type SystemInfo = { name: string; full_name: string; version: string; milestone: string; environment: string };
type ChatMessage = { role: "user" | "assistant"; content: string };
type ChatResponse = { response: string; provider: string; model: string; session_id: string };
type Dashboard = {
  captured_at: string;
  host: { hostname: string; os: string; os_release: string; architecture: string; python: string; cpu_count: number | null };
  cpu: { percent: number | null };
  memory: { total_gb: number | null; used_gb: number | null; percent: number | null };
  disk: { total_gb: number; used_gb: number; free_gb: number; percent: number };
  gpu: { available: boolean; name?: string; utilization_percent?: number; memory_used_mb?: number; memory_total_mb?: number; temperature_c?: number };
  network: { hostname: string; addresses: string[]; adapters: Array<{ name: string; description: string; link_speed: string }> };
  ollama: { online: boolean; models: string[] };
  runtime: {
    api_status: string;
    active_model: string;
    model_provider: string;
    sessions: number;
    tools: Array<{ name: string; description: string; risk: string; requires_approval: boolean }>;
    permissions: { approval_gated: number; automatic: number };
    agents: Array<{ name: string; status: string; kind: string }>;
    queued_tasks: Array<{ name?: string; status?: string }>;
    recent_executions: Array<{ name?: string; status?: string }>;
    projects: Array<{ name: string; status: string; path: string }>;
    objectives: Array<{ name: string; status: string }>;
  };
};

type View = "overview" | "chat";
const API_BASE = import.meta.env.VITE_CHIEF_API_URL || `http://${window.location.hostname}:8000`;
const navItems = [
  [Activity, "Status", "overview"], [MessageSquare, "Chat", "chat"], [BrainCircuit, "Memory", "overview"],
  [Wrench, "Tools", "overview"], [Database, "Projects", "overview"], [Radio, "Sessions", "overview"],
  [ShieldCheck, "Permissions", "overview"], [Settings, "System", "overview"],
] as const;

const clamp = (v: number | null | undefined, fallback = 0) => v == null || Number.isNaN(v) ? fallback : Math.max(0, Math.min(100, v));
const fmtPct = (v: number | null | undefined) => v == null ? "--" : `${Math.round(v)}%`;

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [view, setView] = useState<View>("overview");
  const [clock, setClock] = useState(new Date());
  const [messages, setMessages] = useState<ChatMessage[]>([{ role: "assistant", content: "CHIEF command interface online. Awaiting directive." }]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  async function loadTelemetry() {
    try {
      const [healthRes, systemRes, dashboardRes] = await Promise.all([
        fetch(`${API_BASE}/health`), fetch(`${API_BASE}/system`), fetch(`${API_BASE}/dashboard`),
      ]);
      if (!healthRes.ok || !systemRes.ok || !dashboardRes.ok) throw new Error("CHIEF telemetry unavailable");
      setHealth(await healthRes.json());
      setSystem(await systemRes.json());
      setDashboard(await dashboardRes.json());
      setApiError(null);
    } catch (error) { setApiError(error instanceof Error ? error.message : "Connection failed"); }
  }

  useEffect(() => {
    loadTelemetry();
    const telemetryTimer = window.setInterval(loadTelemetry, 5000);
    const clockTimer = window.setInterval(() => setClock(new Date()), 1000);
    return () => { clearInterval(telemetryTimer); clearInterval(clockTimer); };
  }, []);

  const online = health?.status === "online" && dashboard?.runtime.api_status === "online";
  const activeModel = dashboard?.runtime.active_model || "qwen3:4b";
  const toolCount = dashboard?.runtime.tools.length || 0;
  const approvalCount = dashboard?.runtime.permissions.approval_gated || 0;
  const autoCount = dashboard?.runtime.permissions.automatic || 0;
  const securityPct = toolCount ? Math.round((approvalCount / toolCount) * 100) : 0;
  const overallHealth = useMemo(() => {
    if (!online) return 0;
    const metrics = [dashboard?.cpu.percent, dashboard?.memory.percent, dashboard?.disk.percent, dashboard?.gpu.available ? dashboard.gpu.utilization_percent : null]
      .filter((v): v is number => typeof v === "number");
    return metrics.length ? Math.max(1, Math.round(100 - Math.max(...metrics) * .35)) : 100;
  }, [dashboard, online]);
  const clientIsPhone = /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent);

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || busy) return;
    setMessages((m) => [...m, { role: "user", content: message }]); setInput(""); setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, session_id: sessionId }) });
      if (!res.ok) throw new Error(`Chat request failed (${res.status})`);
      const data: ChatResponse = await res.json();
      setSessionId(data.session_id); setMessages((m) => [...m, { role: "assistant", content: data.response }]); setApiError(null); loadTelemetry();
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "Chat failed");
      setMessages((m) => [...m, { role: "assistant", content: "Unable to reach CHIEF core. Check the API connection." }]);
    } finally { setBusy(false); }
  }

  const recent = dashboard?.runtime.recent_executions || [];
  const tasks = dashboard?.runtime.queued_tasks || [];
  const adapter = dashboard?.network.adapters[0];

  return (
    <main className="shell">
      <header className="topbar hud-frame">
        <div className="brand-block"><div className="brand-mark"><Bot size={25} /></div><div><h1>CHIEF</h1><p>Cognitive Hub for Intelligence, Execution &amp; Foresight</p></div></div>
        <div className="mission-strip">
          <HeaderCell label="SYSTEM STATUS" value={online ? "ACTIVE" : "OFFLINE"} hot={online} />
          <HeaderCell label="CURRENT MILESTONE" value={system?.milestone || "CHIEF ZERO"} sub={system?.environment || "development"} />
          <HeaderCell label="SYSTEM TIME" value={clock.toLocaleTimeString([], { hour12: false })} sub={clock.toLocaleDateString()} />
          <HeaderCell label="OPERATOR" value="DIRECTOR" />
          <HeaderCell label="SESSION ID" value={sessionId ? sessionId.slice(0, 8).toUpperCase() : "UNASSIGNED"} />
        </div>
      </header>

      <section className="workspace">
        <aside className="left-column">
          <Panel title="COMMAND" className="nav-panel"><nav>{navItems.map(([Icon, label, target]) => {
            const active = (view === "chat" && label === "Chat") || (view === "overview" && label === "Status");
            return <button key={label} className={`nav-item ${active ? "active" : ""}`} onClick={() => setView(target)}><Icon size={15}/><span>{label}</span>{active && <ChevronRight size={12} className="push"/>}</button>;
          })}</nav></Panel>

          <Panel title="CHIEF OBJECTIVES" className="objective-panel">
            {(dashboard?.runtime.objectives || []).slice(0, 4).map((o, i) => <Objective key={o.name} name={o.name} status={o.status} primary={i === 0}/>)}
            {!dashboard && <Empty text="Awaiting core telemetry"/>}
          </Panel>

          <Panel title="SECURITY POSTURE" className="compact-panel warning-panel">
            <div className="risk-head"><span>APPROVAL GATED</span><strong>{securityPct}%</strong></div><div className="risk-bar"><span style={{ width: `${securityPct}%` }}/></div>
            <InfoRow label="GUARDED TOOLS" value={String(approvalCount)}/><InfoRow label="AUTOMATIC" value={String(autoCount)}/>
          </Panel>

          <Panel title="COMMS CHANNEL" className="compact-panel comms-panel">
            <div className={`waveform ${online ? "live" : ""}`}>{Array.from({ length: 44 }, (_, i) => <i key={i} style={{ height: `${16 + ((i * 23) % 68)}%` }}/>)}</div>
            <div className="channel-line"><span>API LINK</span><strong>{online ? "SECURE / LIVE" : "NO LINK"}</strong></div>
            <div className="channel-line"><span>HOST</span><strong>{dashboard?.network.hostname || "--"}</strong></div>
            <div className="channel-line"><span>ADAPTER</span><strong>{adapter?.name || "--"}</strong></div>
          </Panel>
        </aside>

        <section className="center-column">
          {view === "overview" ? <>
            <section className="map-panel hud-frame">
              <div className="map-titlebar"><div><span className="eyebrow">CHIEF SYSTEMS GRID</span><h2>{dashboard?.host.hostname || "LOCAL NODE"} // SERVICES // CLIENTS // AGENTS</h2></div><div className="live-badge"><i/> LIVE <b>{dashboard?.network.addresses.length || 0}</b></div></div>
              <NetworkMap dashboard={dashboard} online={online} clientIsPhone={clientIsPhone}/>
              <div className="map-toolbar"><span><Network size={13}/> TOPOLOGY</span><span><Server size={13}/> SERVICES</span><span><BrainCircuit size={13}/> AGENTS</span><span><LockKeyhole size={13}/> PERMISSIONS</span><span className="scan"><i/> SCANNING <b>{online ? "ACTIVE" : "PAUSED"}</b></span></div>
            </section>

            <section className="bottom-grid">
              <Panel title="TASK QUEUE" className="task-panel">{tasks.length ? tasks.slice(0,5).map((t,i)=><TaskRow key={i} index={i+1} text={t.name || "Task"} status={t.status || "queued"}/>) : <Empty text="No queued tasks"/>}<InfoRow label="SESSIONS" value={String(dashboard?.runtime.sessions ?? 0)}/></Panel>
              <div className="middle-bottom">
                <Panel title="SYSTEM TIMELINE" className="timeline-panel"><Timeline online={online} ollama={!!dashboard?.ollama.online} sessions={dashboard?.runtime.sessions || 0}/></Panel>
                <Panel title="COMMAND LOG" className="command-log"><Log time="API" text={online ? "FastAPI core responding" : "Core connection unavailable"} warn={!online}/><Log time="AI" text={`${dashboard?.runtime.model_provider || "ollama"} / ${activeModel}`}/><Log time="NET" text={dashboard?.network.addresses[0] || API_BASE}/>{recent.slice(0,2).map((r,i)=><Log key={i} time={`E${i+1}`} text={r.name || "Execution recorded"}/>)}</Panel>
              </div>
              <Panel title="QUICK ACTIONS" className="quick-panel"><div className="quick-stack"><Action icon={<MessageSquare size={14}/>} label="Open Chat" onClick={()=>setView("chat")}/><Action icon={<RefreshCw size={14}/>} label="Refresh Grid" onClick={loadTelemetry}/><Action icon={<Wrench size={14}/>} label={`${toolCount} Tools`}/><Action icon={<ShieldCheck size={14}/>} label="Permissions"/><Action icon={<Database size={14}/>} label="Projects"/></div></Panel>
            </section>
          </> :
          <section className="chat-panel hud-frame"><div className="map-titlebar"><div><span className="eyebrow">DIRECT CHANNEL</span><h2>CHIEF COMMAND INTERFACE</h2></div><div className="live-badge"><i/> {busy ? "PROCESSING" : "READY"}</div></div><div className="chat-stream">{messages.map((m,i)=><article key={i} className={`message ${m.role}`}><div className="message-tag">{m.role === "assistant" ? "CHIEF" : "OPERATOR"}</div><p>{m.content}</p></article>)}{busy && <article className="message assistant"><div className="message-tag">CHIEF</div><p className="typing">PROCESSING DIRECTIVE...</p></article>}</div><form className="command-input" onSubmit={sendMessage}><TerminalSquare size={16}/><input value={input} onChange={(e)=>setInput(e.target.value)} placeholder="Issue directive to CHIEF..."/><button type="submit" disabled={busy}><Send size={14}/> TRANSMIT</button></form></section>}
        </section>

        <aside className="right-area">
          <div className="telemetry-column">
            <Panel title="SYSTEM HEALTH" className="health-panel"><div className={`health-ring ${online ? "online" : "offline"}`}><div><strong>{online ? `${overallHealth}%` : "--"}</strong><span>{online ? "OPTIMAL" : "NO LINK"}</span></div></div><div className="health-metrics"><Metric label="CPU" value={fmtPct(dashboard?.cpu.percent)} pct={clamp(dashboard?.cpu.percent)}/><Metric label="MEMORY" value={fmtPct(dashboard?.memory.percent)} pct={clamp(dashboard?.memory.percent)}/><Metric label="DISK" value={fmtPct(dashboard?.disk.percent)} pct={clamp(dashboard?.disk.percent)}/><Metric label="GPU" value={dashboard?.gpu.available ? fmtPct(dashboard.gpu.utilization_percent) : "N/A"} pct={clamp(dashboard?.gpu.utilization_percent)}/></div></Panel>

            <Panel title="MODEL INTELLIGENCE" className="model-panel"><div className="model-primary"><BrainCircuit size={27}/><div><span>ACTIVE MODEL</span><strong>{activeModel}</strong></div></div><div className="signal-strip">{Array.from({length:46},(_,i)=><i key={i} style={{height:`${12+((i*17)%72)}%`}}/>)}</div><InfoRow label="ENGINE" value={dashboard?.runtime.model_provider || "OLLAMA"}/><InfoRow label="MODELS" value={String(dashboard?.ollama.models.length || 0)}/><InfoRow label="OLLAMA" value={dashboard?.ollama.online ? "ONLINE" : "OFFLINE"}/></Panel>

            <Panel title="AGENT TELEMETRY" className="agents-panel">{(dashboard?.runtime.agents || []).slice(0,5).map((a,i)=><div className="agent-row" key={a.name}><div><strong>A{i+1} · {a.name}</strong><span>{a.kind}</span></div><b className={a.status === "operational" ? "good" : "bad"}>{a.status}</b><Spark seed={i+2}/></div>)}{!dashboard?.runtime.agents.length && <Empty text="No runtime agents reported"/>}</Panel>

            <Panel title="SYSTEM DIAGNOSTICS" className="diagnostics-panel"><div className="diag-grid"><div><InfoRow label="API" value={online ? "OPERATIONAL" : "OFFLINE"}/><InfoRow label="HOST" value={dashboard?.host.hostname || "--"}/><InfoRow label="OS" value={dashboard?.host.os || "--"}/><InfoRow label="CORES" value={String(dashboard?.host.cpu_count ?? "--")}/><InfoRow label="TOOLS" value={String(toolCount)}/><InfoRow label="SESSIONS" value={String(dashboard?.runtime.sessions ?? 0)}/></div><div><GaugeBlock icon={<Cpu size={13}/>} label="CPU" value={fmtPct(dashboard?.cpu.percent)}/><GaugeBlock icon={<MemoryStick size={13}/>} label="RAM" value={dashboard?.memory.total_gb ? `${dashboard.memory.used_gb}/${dashboard.memory.total_gb} GB` : "--"}/><GaugeBlock icon={<HardDrive size={13}/>} label="DISK FREE" value={dashboard ? `${dashboard.disk.free_gb} GB` : "--"}/><GaugeBlock icon={<Zap size={13}/>} label="GPU TEMP" value={dashboard?.gpu.temperature_c != null ? `${dashboard.gpu.temperature_c}°C` : "--"}/></div></div></Panel>
          </div>

          <div className="utility-column">
            <Panel title="ACTIVE SERVICES" className="services-panel"><ServiceRow label="CHIEF CORE" value={online ? "ONLINE" : "OFFLINE"} good={online}/><ServiceRow label="FASTAPI" value={dashboard?.runtime.api_status || "--"} good={online}/><ServiceRow label="OLLAMA" value={dashboard?.ollama.online ? "ONLINE" : "OFFLINE"} good={!!dashboard?.ollama.online}/><ServiceRow label="TOOL REGISTRY" value={`${toolCount} TOOLS`} good/><ServiceRow label="MEMORY" value="LOCAL" good/></Panel>

            <Panel title="NETWORK STATUS" className="network-status-panel"><div className="mini-map"><Network size={42}/><span>{clientIsPhone ? "MOBILE CLIENT" : "DESKTOP CLIENT"}</span><strong>{dashboard?.network.addresses[0] || "NO ADDRESS"}</strong></div><InfoRow label="HOST" value={dashboard?.network.hostname || "--"}/><InfoRow label="LINK" value={adapter?.link_speed || "--"}/><InfoRow label="ADAPTER" value={adapter?.name || "--"}/></Panel>

            <Panel title="ALERTS FEED" className="alerts-panel">{apiError && <Alert text={apiError}/>} {!dashboard?.ollama.online && <Alert text="Ollama service is offline"/>}{dashboard?.gpu.available === false && <Alert text="GPU telemetry unavailable" mild/>}{!apiError && dashboard?.ollama.online && dashboard?.gpu.available !== false && <div className="clear"><ShieldCheck size={14}/> No active system alerts</div>}</Panel>

            <Panel title="SHORTCUTS" className="shortcuts-panel"><div className="shortcut-grid"><Action icon={<MessageSquare size={13}/>} label="Chat" onClick={()=>setView("chat")}/><Action icon={<Database size={13}/>} label="Projects"/><Action icon={<Wrench size={13}/>} label="Tools"/><Action icon={<ShieldCheck size={13}/>} label="Audit"/><Action icon={<RefreshCw size={13}/>} label="Refresh" onClick={loadTelemetry}/><Action icon={<Settings size={13}/>} label="Settings"/></div></Panel>
          </div>
        </aside>
      </section>

      <footer className="footer-bar"><span><i className={online ? "online-dot" : "offline-dot"}/> LINK: {online ? "SECURE" : "DISCONNECTED"}</span><span>NODE: {dashboard?.host.hostname || "CHIEF-LOCAL"}</span><span>MODEL: {activeModel}</span><span>DATA INTEGRITY: {dashboard ? "VERIFIED" : "PENDING"}</span><span>CONTROL: LOCAL</span><span>ACCESS: DIRECTOR</span></footer>
    </main>
  );
}

function NetworkMap({ dashboard, online, clientIsPhone }: { dashboard: Dashboard | null; online: boolean; clientIsPhone: boolean }) {
  const host = dashboard?.host.hostname || "CHIEF HOST";
  const model = dashboard?.runtime.active_model || "LOCAL MODEL";
  const ip = dashboard?.network.addresses[0] || "127.0.0.1";
  const toolCount = dashboard?.runtime.tools.length || 0;
  const sessions = dashboard?.runtime.sessions || 0;
  const nodes = [
    { x: 50, y: 50, label: "CHIEF CORE", sub: online ? "ONLINE" : "OFFLINE", tone: "core" },
    { x: 50, y: 13, label: host.toUpperCase(), sub: ip, tone: "host" },
    { x: 79, y: 27, label: "OLLAMA", sub: dashboard?.ollama.online ? "SERVICE LIVE" : "OFFLINE", tone: "service" },
    { x: 83, y: 59, label: model.toUpperCase(), sub: "ACTIVE MODEL", tone: "agent" },
    { x: 66, y: 82, label: "TOOL REGISTRY", sub: `${toolCount} TOOLS`, tone: "service" },
    { x: 35, y: 82, label: "MEMORY", sub: "LOCAL STORE", tone: "agent" },
    { x: 17, y: 63, label: clientIsPhone ? "PHONE CLIENT" : "DESKTOP CLIENT", sub: "OPERATOR LINK", tone: "client" },
    { x: 18, y: 29, label: "FASTAPI", sub: "PORT 8000", tone: "service" },
    { x: 34, y: 17, label: "SESSIONS", sub: `${sessions} ACTIVE`, tone: "client" },
  ];
  const center = nodes[0];
  return <div className="network-map"><div className="urban-grid"/><div className="grid-floor"/><div className="radar-ring r1"/><div className="radar-ring r2"/><div className="radar-ring r3"/><div className="radar-ring r4"/><div className="radar-ring r5"/><div className="crosshair horizontal"/><div className="crosshair vertical"/><span className="compass north">N</span><span className="compass east">E</span><span className="compass south">S</span><span className="compass west">W</span><svg className="connection-layer" viewBox="0 0 100 100" preserveAspectRatio="none">{nodes.slice(1).map((n,i)=><g key={n.label}><line x1={center.x} y1={center.y} x2={n.x} y2={n.y} className={i % 3 === 1 ? "connection hot" : "connection"}/><circle cx={n.x} cy={n.y} r=".55" className="pulse-point"/></g>)}</svg>{nodes.map((n,i)=><div key={n.label} className={`map-node ${n.tone} ${i===0?"center-node":""}`} style={{left:`${n.x}%`,top:`${n.y}%`}}><div className="node-icon">{i===0?<Bot size={16}/>:i===1?<Laptop size={13}/>:i===6&&clientIsPhone?<Smartphone size={13}/>:i===3||i===5?<BrainCircuit size={13}/>:<Server size={13}/>}</div><div className="node-label"><strong>{n.label}</strong><span>{n.sub}</span></div></div>)}<div className="map-readout left-readout"><span>HOST ADDRESS</span><strong>{ip}</strong><small>{dashboard?.network.adapters[0]?.link_speed || "LOCAL NETWORK"}</small></div><div className="map-readout right-readout"><span>MODEL SERVICE</span><strong>{dashboard?.ollama.online ? "OLLAMA ONLINE" : "OLLAMA OFFLINE"}</strong><small>{dashboard?.ollama.models.length || 0} MODELS INSTALLED</small></div><div className="map-readout lower-readout"><span>PERMISSIONS</span><strong>{dashboard?.runtime.permissions.approval_gated || 0} GUARDED</strong><small>{dashboard?.runtime.permissions.automatic || 0} AUTOMATIC</small></div></div>;
}

function HeaderCell({label,value,sub,hot=false}:{label:string;value:string;sub?:string;hot?:boolean}){return <div className="header-cell"><span>{label}</span><strong className={hot?"teal":""}>{value}</strong>{sub&&<small>{sub}</small>}</div>}
function Panel({title,children,className=""}:{title:string;children:React.ReactNode;className?:string}){return <section className={`panel hud-frame ${className}`}><div className="panel-title"><span>{title}</span><i/></div>{children}</section>}
function Objective({name,status,primary}:{name:string;status:string;primary?:boolean}){return <div className={`objective ${primary?"primary":""}`}><span>{primary?"PRIMARY":"SECONDARY"}</span><strong>{name}</strong><small><i className="check"/> {status.toUpperCase()}</small></div>}
function Metric({label,value,pct}:{label:string;value:string;pct:number}){return <div className="metric"><div><span>{label}</span><strong>{value}</strong></div><div className="metric-track"><span style={{width:`${pct}%`}}/></div></div>}
function InfoRow({label,value}:{label:string;value:string}){return <div className="info-row"><span>{label}</span><strong>{value}</strong></div>}
function Log({time,text,warn=false}:{time:string;text:string;warn?:boolean}){return <div className="log-row"><code>[{time}]</code><span className={warn?"warn":""}>{text}</span></div>}
function Action({icon,label,onClick}:{icon:React.ReactNode;label:string;onClick?:()=>void}){return <button className="action-button" onClick={onClick}>{icon}<span>{label}</span></button>}
function GaugeBlock({icon,label,value}:{icon:React.ReactNode;label:string;value:string}){return <div className="gauge-block"><span>{icon}{label}</span><strong>{value}</strong></div>}
function Spark({seed}:{seed:number}){return <div className="spark">{Array.from({length:15},(_,i)=><i key={i} style={{height:`${10+((i*19+seed*13)%75)}%`}}/>)}</div>}
function Alert({text,mild=false}:{text:string;mild?:boolean}){return <div className={`alert ${mild?"mild":""}`}><AlertTriangle size={13}/><span>{text}</span></div>}
function Empty({text}:{text:string}){return <div className="empty-state">{text}</div>}
function ServiceRow({label,value,good=false}:{label:string;value:string;good?:boolean}){return <div className="service-row"><i className={good?"service-dot good":"service-dot"}/><span>{label}</span><strong>{value}</strong></div>}
function TaskRow({index,text,status}:{index:number;text:string;status:string}){return <div className="task-row"><b>{String(index).padStart(2,"0")}</b><div><strong>{text}</strong><i><span style={{width:`${30+index*12}%`}}/></i></div><small>{status}</small></div>}
function Timeline({online,ollama,sessions}:{online:boolean;ollama:boolean;sessions:number}){return <div className="timeline"><div className="timeline-line"/><div className="timeline-step done"><i/><span>CORE</span><small>{online?"ONLINE":"OFFLINE"}</small></div><div className={`timeline-step ${ollama?"done":"warn-step"}`}><i/><span>MODEL</span><small>{ollama?"READY":"OFFLINE"}</small></div><div className="timeline-step live-step"><i/><span>SESSION</span><small>{sessions} ACTIVE</small></div><div className="timeline-step"><i/><span>TASKS</span><small>STANDBY</small></div></div>}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);