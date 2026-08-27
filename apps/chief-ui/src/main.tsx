import React, { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity, AlertTriangle, Bot, BrainCircuit, ChevronRight, Cpu, Database,
  HardDrive, Laptop, LockKeyhole, MemoryStick, MessageSquare, Network, Radio,
  RefreshCw, Send, Server, Settings, ShieldCheck, Smartphone, TerminalSquare,
  Wrench, Zap, Crosshair, Layers3, ScanLine, FolderGit2, Gauge, Router,
  Download, Mic, MicOff, Square, Volume2, VolumeX, BriefcaseBusiness,
  Building2, Cable, CircleDot, Landmark, UserRoundCog,
} from "lucide-react";
import "./styles.css";
import { ChiefApiError, hasChiefApiToken, requestJson, setChiefApiToken } from "./api";
import { useBrowserVoice, type BrowserVoiceControls } from "./useBrowserVoice";

type Health = { status: string; system: string; version: string };
type SystemInfo = { name: string; full_name: string; version: string; milestone: string; environment: string };
type ChatMessage = { role: "user" | "chief" | "ultron"; content: string };
type AgentChatMessage = { speaker: "CHIEF" | "ULTRON"; content: string; provider: string; model: string };
type ChatResponse = { response: string; provider: string; model: string; session_id: string; messages?: AgentChatMessage[] };
type PortfolioSummary = {
  owner_id: string;
  businesses: number;
  agents: number;
  systems: number;
  financial_accounts: number;
  active_agents: number;
  execution_enabled_agents: number;
  external_write_enabled_systems: number;
  healthy_agents: number;
  is_blank: boolean;
};
type PortfolioOnboardingStep = {
  key: string;
  title: string;
  complete: boolean;
  requires_human: boolean;
  description?: string;
};
type PortfolioOnboarding = {
  owner_id: string;
  is_blank: boolean;
  ready_for_autonomy: boolean;
  next_step: string | null;
  steps: PortfolioOnboardingStep[];
};
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
    recent_executions: Array<{ name?: string; status?: string; tool_name?: string; success?: boolean }>;
    projects: Array<{ name: string; status: string; path: string }>;
    objectives: Array<{ name: string; status: string }>;
    portfolio_summary?: PortfolioSummary;
    portfolio_onboarding?: PortfolioOnboarding;
  };
};

type View = "overview" | "portfolio" | "chat";
const DEFAULT_API_PROTOCOL = window.location.protocol === "https:" ? "https:" : "http:";
const API_BASE = import.meta.env.VITE_CHIEF_API_URL || `${DEFAULT_API_PROTOCOL}//${window.location.hostname}:8000`;
const API_URL = new URL(API_BASE, window.location.href);
const API_IS_LOOPBACK = ["localhost", "127.0.0.1", "::1"].includes(API_URL.hostname);
const API_IS_PROTECTED = API_URL.protocol === "https:";
const PAIRING_TRANSPORT_SAFE = API_IS_LOOPBACK || API_IS_PROTECTED;

interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
}

const navItems = [
  [Activity, "Status", "overview"], [BriefcaseBusiness, "Portfolio", "portfolio"],
  [MessageSquare, "Chat", "chat"], [BrainCircuit, "Memory", "overview"],
  [Wrench, "Tools", "overview"], [FolderGit2, "Projects", "overview"], [Radio, "Sessions", "overview"],
  [ShieldCheck, "Permissions", "overview"], [Settings, "System", "overview"],
] as const;
const clamp = (v: number | null | undefined, fallback = 0) => v == null || Number.isNaN(v) ? fallback : Math.max(0, Math.min(100, v));
const fmtPct = (v: number | null | undefined) => v == null ? "--" : `${Math.round(v)}%`;

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [view, setView] = useState<View>(() => {
    const requested = new URLSearchParams(window.location.search).get("view");
    return requested === "chat" || requested === "portfolio" ? requested : "overview";
  });
  const [clock, setClock] = useState(new Date());
  const [messages, setMessages] = useState<ChatMessage[]>([{ role: "chief", content: "CHIEF command interface online. Awaiting directive." }]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(() => sessionStorage.getItem("chief.session"));
  const [busy, setBusy] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [pairingRequired, setPairingRequired] = useState(
    () => !API_IS_LOOPBACK && !hasChiefApiToken(),
  );
  const [pairingToken, setPairingToken] = useState("");
  const [installPrompt, setInstallPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [appInstalled, setAppInstalled] = useState(
    () => window.matchMedia("(display-mode: standalone)").matches,
  );
  const chatAbort = useRef<AbortController | null>(null);
  const voice = useBrowserVoice(setInput);

  async function loadTelemetry() {
    try {
      const [nextHealth, nextSystem, nextDashboard] = await Promise.all([
        requestJson<Health>(`${API_BASE}/health`, {}, 5000), requestJson<SystemInfo>(`${API_BASE}/system`, {}, 5000), requestJson<Dashboard>(`${API_BASE}/dashboard`, {}, 8000),
      ]);
      setHealth(nextHealth); setSystem(nextSystem); setDashboard(nextDashboard); setApiError(null);
    } catch (error) {
      if (error instanceof ChiefApiError && error.status === 401) setPairingRequired(true);
      setApiError(error instanceof Error ? error.message : "Connection failed");
    }
  }

  useEffect(() => {
    loadTelemetry();
    const refresh = () => { if (!document.hidden) void loadTelemetry(); };
    const telemetryTimer = window.setInterval(refresh, 8000);
    const clockTimer = window.setInterval(() => setClock(new Date()), 1000);
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("online", refresh);
    const offline = () => setApiError("Network connection offline");
    window.addEventListener("offline", offline);
    return () => { clearInterval(telemetryTimer); clearInterval(clockTimer); document.removeEventListener("visibilitychange", refresh); window.removeEventListener("online", refresh); window.removeEventListener("offline", offline); chatAbort.current?.abort(); };
  }, []);

  useEffect(() => {
    const captureInstallPrompt = (event: Event) => {
      event.preventDefault();
      setInstallPrompt(event as BeforeInstallPromptEvent);
    };
    const markInstalled = () => {
      setAppInstalled(true);
      setInstallPrompt(null);
    };
    window.addEventListener("beforeinstallprompt", captureInstallPrompt);
    window.addEventListener("appinstalled", markInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", captureInstallPrompt);
      window.removeEventListener("appinstalled", markInstalled);
    };
  }, []);

  async function installApp() {
    if (!installPrompt) return;
    await installPrompt.prompt();
    const choice = await installPrompt.userChoice;
    if (choice.outcome === "accepted") setAppInstalled(true);
    setInstallPrompt(null);
  }

  function pairApi(event: FormEvent) {
    event.preventDefault();
    if (!PAIRING_TRANSPORT_SAFE) {
      setApiError("Pairing is blocked until CHIEF Core is reached through HTTPS or localhost");
      return;
    }
    const token = pairingToken.trim();
    if (token.length < 32) {
      setApiError("Pairing token must contain at least 32 characters");
      return;
    }
    setChiefApiToken(token);
    setPairingToken("");
    setPairingRequired(false);
    setApiError(null);
    void loadTelemetry();
  }

  function forgetApiToken() {
    setChiefApiToken("");
    setPairingRequired(!API_IS_LOOPBACK);
    setApiError("This tab is no longer paired with CHIEF Core");
  }

  const online = health?.status === "online" && dashboard?.runtime.api_status === "online";
  const activeModel = dashboard?.runtime.active_model || "LOCAL MODEL";
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
  const recent = dashboard?.runtime.recent_executions || [];
  const tasks = dashboard?.runtime.queued_tasks || [];
  const adapter = dashboard?.network.adapters[0];
  const apiLinkLabel = online
    ? API_IS_PROTECTED
      ? "PROTECTED / LIVE"
      : API_IS_LOOPBACK
        ? "LOCAL / LIVE"
        : "UNPROTECTED / LIVE"
    : "NO LINK";
  const pwaStatus = appInstalled ? "INSTALLED" : installPrompt ? "INSTALL READY" : "BROWSER INSTALL";
  const alerts = useMemo(() => {
    const rows: Array<{ text: string; mild?: boolean }> = [];
    if (apiError) rows.push({ text: apiError });
    if (dashboard && !dashboard.ollama.online) rows.push({ text: "Ollama service offline" });
    if (dashboard?.cpu.percent != null && dashboard.cpu.percent > 85) rows.push({ text: `CPU load high: ${fmtPct(dashboard.cpu.percent)}` });
    if (dashboard?.memory.percent != null && dashboard.memory.percent > 88) rows.push({ text: `Memory pressure: ${fmtPct(dashboard.memory.percent)}` });
    if (dashboard?.disk.percent != null && dashboard.disk.percent > 90) rows.push({ text: `Disk utilization: ${fmtPct(dashboard.disk.percent)}` });
    if (dashboard?.gpu.available === false) rows.push({ text: "GPU telemetry unavailable", mild: true });
    if (online && !API_IS_PROTECTED && !API_IS_LOOPBACK) rows.push({ text: "API link uses unprotected HTTP", mild: true });
    return rows;
  }, [apiError, dashboard, online]);

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || busy) return;
    setMessages((m) => [...m, { role: "user", content: message }]); setInput(""); setBusy(true);
    try {
      chatAbort.current = new AbortController();
      const data = await requestJson<ChatResponse>(`${API_BASE}/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, session_id: sessionId }), signal: chatAbort.current.signal }, 130000);
      const replies: ChatMessage[] = data.messages?.length
        ? data.messages.map((reply) => ({ role: reply.speaker === "ULTRON" ? "ultron" : "chief", content: reply.content }))
        : [{ role: "chief", content: data.response }];
      setSessionId(data.session_id); sessionStorage.setItem("chief.session", data.session_id); setMessages((m) => [...m, ...replies]); setApiError(null); void loadTelemetry();
      voice.speak(replies.map((reply) => `${reply.role === "ultron" ? "Ultron" : "Chief"}. ${reply.content}`).join(" "));
    } catch (error) {
      setApiError(error instanceof Error ? error.message : "Chat failed");
      setMessages((m) => [...m, { role: "chief", content: "Unable to reach CHIEF core. Check the API connection." }]);
    } finally { setBusy(false); chatAbort.current = null; }
  }

  return <main className="c2-shell">
    <header className="c2-top hud-frame">
      <div className="c2-brand"><div className="c2-brand-glyph"><Bot size={36}/></div><div><h1>CHIEF</h1><p>Cognitive Hub for Intelligence, Execution &amp; Foresight</p></div></div>
      <div className="c2-head-cells">
        <HeaderCell label="// SYSTEM STATUS" value={online ? "ACTIVE" : "OFFLINE"} hot={online}/>
        <HeaderCell label="CURRENT MILESTONE" value={system?.milestone || "CHIEF ZERO"} sub={`${system?.environment || "development"} / ${dashboard?.host.hostname || "local"}`}/>
        <HeaderCell label="SYSTEM TIME" value={clock.toLocaleDateString()} sub={clock.toLocaleTimeString([], { hour12: false })}/>
        <HeaderCell label="OPERATOR" value="DIRECTOR"/>
        <HeaderCell label="SESSION ID" value={sessionId ? sessionId.slice(0, 8).toUpperCase() : "UNASSIGNED"}/>
      </div>
    </header>

    {(pairingRequired || hasChiefApiToken()) && <section className="pairing-strip hud-frame" aria-label="CHIEF Core device pairing">
      {pairingRequired ? <form onSubmit={pairApi}>
        <LockKeyhole size={18}/>
        <label htmlFor="chief-pairing-token">PAIR THIS TAB WITH CHIEF CORE</label>
        <input id="chief-pairing-token" type="password" value={pairingToken} onChange={(event)=>setPairingToken(event.target.value)} minLength={32} autoComplete="off" placeholder="Paste the 32+ character pairing token" disabled={!PAIRING_TRANSPORT_SAFE}/>
        <button type="submit" disabled={!PAIRING_TRANSPORT_SAFE}>PAIR</button>
        <small>{PAIRING_TRANSPORT_SAFE ? "Kept only for this browser tab session; never included in the app build or offline cache." : "Pairing is blocked on plain remote HTTP. Connect through HTTPS or an encrypted private tunnel first."}</small>
      </form> : <div className="paired-state"><ShieldCheck size={17}/><span>THIS TAB IS PAIRED</span><button type="button" onClick={forgetApiToken}>FORGET TOKEN</button></div>}
    </section>}

    <section className="c2-workspace">
      <aside className="c2-left">
        <Panel title="COMMAND" className="c2-nav"><nav>{navItems.map(([Icon,label,target]) => {
          const active = view === target && (target !== "overview" || label === "Status");
          return <button key={label} className={`nav-item ${active ? "active" : ""}`} onClick={() => setView(target)}><Icon size={18}/><span>{label}</span>{active && <ChevronRight size={14} className="push"/>}</button>;
        })}</nav></Panel>
        <Panel title="CHIEF OBJECTIVES" className="c2-objectives">
          {(dashboard?.runtime.objectives || []).slice(0,4).map((o,i)=><Objective key={o.name} name={o.name} status={o.status} primary={i===0}/>)}
          {!dashboard && <Empty text="Awaiting core telemetry"/>}
        </Panel>
        <Panel title="SECURITY POSTURE" className="c2-security">
          <div className="risk-head"><span>APPROVAL GATED</span><strong>{securityPct}%</strong></div><div className="risk-bar"><span style={{width:`${securityPct}%`}}/></div>
          <InfoRow label="GUARDED TOOLS" value={String(approvalCount)}/><InfoRow label="AUTOMATIC" value={String(autoCount)}/>
        </Panel>
        <Panel title="COMMS CHANNEL" className="c2-comms">
          <div className={`waveform ${online ? "live" : ""}`}>{Array.from({length:48},(_,i)=><i key={i} style={{height:`${18+((i*29)%68)}%`}}/>)}</div>
          <div className="comms-icons"><span><Crosshair/></span><span><Radio/></span><span><MessageSquare/></span><span><Network/></span></div>
          <InfoRow label="API LINK" value={apiLinkLabel}/><InfoRow label="HOST" value={dashboard?.network.hostname || "--"}/><InfoRow label="ADAPTER" value={adapter?.name || "--"}/>
        </Panel>
      </aside>

      <section className="c2-center">
        {view === "overview" ? <>
          <section className="c2-map hud-frame">
            <div className="c2-map-head"><div><span className="eyebrow">CHIEF SYSTEMS GRID</span><h2>{dashboard?.host.hostname || "LOCAL NODE"} / SERVICES / MODELS / TOOLS / PROJECTS</h2></div><div className="live-badge"><i/> LIVE <b>{dashboard?.network.addresses.length || 0}</b></div></div>
            <NetworkMap dashboard={dashboard} online={online} clientIsPhone={clientIsPhone}/>
            <div className="c2-map-tools"><span><Crosshair/>TOPOLOGY</span><span><Layers3/>LAYERS</span><span><Network/>LINKS</span><span><LockKeyhole/>PERMISSIONS</span><span className="scan"><ScanLine/> SCANNING <b>{online ? "ACTIVE" : "PAUSED"}</b></span></div>
          </section>
          <section className="c2-bottom">
            <Panel title="TASK QUEUE" className="c2-task">{tasks.length ? tasks.slice(0,5).map((t,i)=><TaskRow key={i} index={i+1} text={t.name || "Pending approval"} status={t.status || "queued"}/>) : <><Empty text="No queued approvals"/><InfoRow label="ACTIVE SESSIONS" value={String(dashboard?.runtime.sessions ?? 0)}/><InfoRow label="PROJECTS" value={String(dashboard?.runtime.projects.length ?? 0)}/></>}</Panel>
            <div className="c2-midbottom">
              <Panel title="SYSTEM TIMELINE"><Timeline online={online} ollama={!!dashboard?.ollama.online} sessions={dashboard?.runtime.sessions || 0} executions={recent.length}/></Panel>
              <Panel title="COMMAND LOG" className="c2-command-log"><Log time="API" text={online ? "FastAPI core responding" : "Core unavailable"} warn={!online}/><Log time="AI" text={`${dashboard?.runtime.model_provider || "ollama"} / ${activeModel}`}/>{recent.slice(0,3).map((r,i)=><Log key={i} time={`E${i+1}`} text={r.name || r.tool_name || "Execution recorded"}/>)}</Panel>
            </div>
            <Panel title="QUICK ACTIONS" className="c2-quick"><Action icon={<Crosshair/>} label="System Grid"/><Action icon={<RefreshCw/>} label="Refresh Data" onClick={loadTelemetry}/><Action icon={<MessageSquare/>} label="Open Chat" onClick={()=>setView("chat")}/><Action icon={<Wrench/>} label={`${toolCount} Tools`}/><Action icon={<ShieldCheck/>} label="Permissions"/><Action icon={<Download/>} label={pwaStatus} onClick={installPrompt ? installApp : undefined}/></Panel>
          </section>
        </> : view === "portfolio" ? <PortfolioPanel
          summary={dashboard?.runtime.portfolio_summary}
          onboarding={dashboard?.runtime.portfolio_onboarding}
          online={online}
          onRefresh={()=>void loadTelemetry()}
          onBegin={()=>{setInput("Help me define my first business portfolio.");setView("chat");}}
        /> : <ChatPanel messages={messages} input={input} setInput={setInput} sendMessage={sendMessage} busy={busy} voice={voice} appInstalled={appInstalled} installReady={Boolean(installPrompt)} installApp={installApp}/>}
      </section>

      <aside className="c2-right">
        <div className="c2-right-main">
          <Panel title="SYSTEM HEALTH" className="c2-health"><div className={`health-ring ${online ? "online" : "offline"}`}><div><strong>{online ? `${overallHealth}%` : "--"}</strong><span>{online ? "OPTIMAL" : "NO LINK"}</span></div></div><div className="health-metrics"><Metric label="CPU" value={fmtPct(dashboard?.cpu.percent)} pct={clamp(dashboard?.cpu.percent)}/><Metric label="MEMORY" value={fmtPct(dashboard?.memory.percent)} pct={clamp(dashboard?.memory.percent)}/><Metric label="DISK" value={fmtPct(dashboard?.disk.percent)} pct={clamp(dashboard?.disk.percent)}/><Metric label="GPU" value={dashboard?.gpu.available ? fmtPct(dashboard.gpu.utilization_percent) : "N/A"} pct={clamp(dashboard?.gpu.utilization_percent)}/></div></Panel>
          <Panel title="MODEL INTELLIGENCE" className="c2-model"><div className="model-primary"><BrainCircuit size={32}/><div><span>ACTIVE MODEL</span><strong>{activeModel}</strong></div></div><div className="signal-strip">{Array.from({length:56},(_,i)=><i key={i} style={{height:`${12+((i*17)%72)}%`}}/>)}</div><InfoRow label="ENGINE" value={dashboard?.runtime.model_provider || "OLLAMA"}/><InfoRow label="INSTALLED MODELS" value={String(dashboard?.ollama.models.length || 0)}/><InfoRow label="SERVICE" value={dashboard?.ollama.online ? "STRONG" : "OFFLINE"}/></Panel>
          <Panel title="AGENT TELEMETRY" className="c2-agents">{(dashboard?.runtime.agents || []).slice(0,6).map((a,i)=><div className="agent-row" key={a.name}><div><strong>A{i+1} · {a.name}</strong><span>{a.kind}</span></div><b className={a.status === "operational" ? "good" : "bad"}>{a.status}</b><Spark seed={i+2}/></div>)}{!dashboard?.runtime.agents.length && <Empty text="No runtime agents reported"/>}</Panel>
          <Panel title="SYSTEM DIAGNOSTICS" className="c2-diagnostics"><div className="diag-grid"><div><InfoRow label="API" value={online ? "OPERATIONAL" : "OFFLINE"}/><InfoRow label="HOST" value={dashboard?.host.hostname || "--"}/><InfoRow label="OS" value={`${dashboard?.host.os || "--"} ${dashboard?.host.os_release || ""}`}/><InfoRow label="CORES" value={String(dashboard?.host.cpu_count ?? "--")}/><InfoRow label="TOOLS" value={String(toolCount)}/><InfoRow label="SESSIONS" value={String(dashboard?.runtime.sessions ?? 0)}/></div><div className="diag-gauges"><GaugeBlock icon={<Cpu/>} label="CPU" value={fmtPct(dashboard?.cpu.percent)}/><GaugeBlock icon={<MemoryStick/>} label="RAM" value={dashboard?.memory.total_gb ? `${dashboard.memory.used_gb}/${dashboard.memory.total_gb} GB` : "--"}/><GaugeBlock icon={<Zap/>} label="GPU TEMP" value={dashboard?.gpu.temperature_c != null ? `${dashboard.gpu.temperature_c}°C` : "--"}/><GaugeBlock icon={<HardDrive/>} label="DISK FREE" value={dashboard ? `${dashboard.disk.free_gb} GB` : "--"}/></div></div></Panel>
        </div>
        <div className="c2-right-side">
          <Panel title="ACTIVE SERVICES" className="c2-services"><ServiceRow label="CHIEF CORE" value={online ? "ONLINE" : "OFFLINE"} good={online}/><ServiceRow label="FASTAPI" value={dashboard?.runtime.api_status || "--"} good={online}/><ServiceRow label="OLLAMA" value={dashboard?.ollama.online ? "ONLINE" : "OFFLINE"} good={!!dashboard?.ollama.online}/><ServiceRow label="TOOL REGISTRY" value={`${toolCount} TOOLS`} good/><ServiceRow label="MEMORY" value="LOCAL" good/></Panel>
          <Panel title="NETWORK STATUS" className="c2-network"><div className="network-visual"><Router size={54}/><span>{clientIsPhone ? "MOBILE CLIENT" : "DESKTOP CLIENT"}</span><strong>{dashboard?.network.addresses[0] || "NO ADDRESS"}</strong></div><InfoRow label="HOST" value={dashboard?.network.hostname || "--"}/><InfoRow label="LINK" value={adapter?.link_speed || "--"}/><InfoRow label="ADAPTER" value={adapter?.name || "--"}/></Panel>
          <Panel title="ALERTS FEED" className="c2-alerts">{alerts.length ? alerts.slice(0,5).map((a,i)=><Alert key={i} text={a.text} mild={a.mild}/>) : <div className="clear"><ShieldCheck size={15}/> No active system alerts</div>}</Panel>
          <Panel title="SHORTCUTS" className="c2-shortcuts"><div className="shortcut-grid"><Action icon={<BriefcaseBusiness/>} label="Portfolio" onClick={()=>setView("portfolio")}/><Action icon={<MessageSquare/>} label="Chat" onClick={()=>setView("chat")}/><Action icon={<Wrench/>} label="Tools"/><Action icon={<ShieldCheck/>} label="Audit"/><Action icon={<RefreshCw/>} label="Refresh" onClick={loadTelemetry}/><Action icon={<Download/>} label={pwaStatus} onClick={installPrompt ? installApp : undefined}/></div></Panel>
        </div>
      </aside>
    </section>

    <footer className="c2-footer"><span className={online && !API_IS_PROTECTED && !API_IS_LOOPBACK ? "link-warning" : ""}><i className={online ? "online-dot" : "offline-dot"}/> LINK: {apiLinkLabel}</span><span>NODE: {dashboard?.host.hostname || "CHIEF-LOCAL"}</span><span>MODEL: {activeModel}</span><span>DATA INTEGRITY: {dashboard ? "REPORTED" : "PENDING"}</span><span>CONTROL: {API_IS_LOOPBACK ? "LOCAL" : "NETWORK"}</span><span>ACCESS: NO SIGN-IN</span></footer>
  </main>;
}

function NetworkMap({dashboard,online,clientIsPhone}:{dashboard:Dashboard|null;online:boolean;clientIsPhone:boolean}) {
  const host = dashboard?.host.hostname || "CHIEF HOST";
  const ip = dashboard?.network.addresses[0] || "127.0.0.1";
  const fixed = [
    {x:50,y:50,label:"CHIEF CORE",sub:online?"ONLINE":"OFFLINE",tone:"core",icon:"core"},
    {x:50,y:13,label:host.toUpperCase(),sub:ip,tone:"host",icon:"host"},
    {x:77,y:24,label:"OLLAMA",sub:dashboard?.ollama.online?"SERVICE LIVE":"OFFLINE",tone:"service",icon:"service"},
    {x:84,y:50,label:(dashboard?.runtime.active_model||"MODEL").toUpperCase(),sub:"ACTIVE MODEL",tone:"agent",icon:"agent"},
    {x:70,y:80,label:"TOOL REGISTRY",sub:`${dashboard?.runtime.tools.length||0} TOOLS`,tone:"service",icon:"service"},
    {x:31,y:81,label:"MEMORY",sub:"LOCAL STORE",tone:"agent",icon:"agent"},
    {x:16,y:63,label:clientIsPhone?"PHONE CLIENT":"DESKTOP CLIENT",sub:"OPERATOR LINK",tone:"client",icon:"client"},
    {x:18,y:27,label:"FASTAPI",sub:"PORT 8000",tone:"service",icon:"service"},
  ];
  const extras = [
    ...(dashboard?.runtime.projects || []).slice(0,2).map((p,i)=>({x:[35,64][i],y:[17,84][i],label:p.name.toUpperCase(),sub:"PROJECT",tone:"project",icon:"project"})),
    ...(dashboard?.ollama.models || []).filter(m=>m!==dashboard?.runtime.active_model).slice(0,2).map((m,i)=>({x:[30,75][i],y:[39,67][i],label:m.toUpperCase(),sub:"MODEL INSTALLED",tone:"model",icon:"agent"})),
  ];
  const nodes = [...fixed,...extras]; const center = nodes[0];
  return <div className="concept-map">
    <div className="map-city"/><div className="map-grid"/><div className="map-scan"/>
    {[18,29,40,52,65,78].map((w,i)=><div key={w} className={`concept-ring ring-${i}`} style={{width:`${w}%`}}/>)}
    <div className="crosshair horizontal"/><div className="crosshair vertical"/>
    <span className="compass north">N</span><span className="compass east">E</span><span className="compass south">S</span><span className="compass west">W</span>
    <svg className="connection-layer" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
      {nodes.slice(1).map((n,i)=><g key={`${n.label}-${i}`}><line x1={center.x} y1={center.y} x2={n.x} y2={n.y} className={i%3===1?"connection hot":"connection"}/><circle cx={n.x} cy={n.y} r=".5" className="pulse-point"/></g>)}
      {Array.from({length:18},(_,i)=>{const a=(i*37)%100,b=(i*53)%100,c=(i*71)%100,d=(i*29)%100;return <line key={i} x1={a} y1={b} x2={c} y2={d} className="ambient-link"/>})}
    </svg>
    {Array.from({length:22},(_,i)=><span key={i} className="map-beacon" style={{left:`${8+((i*31)%84)}%`,top:`${10+((i*47)%78)}%`}}/>) }
    {nodes.map((n,i)=><div key={`${n.label}-${i}`} className={`map-node ${n.tone} ${i===0?"center-node":""}`} style={{left:`${n.x}%`,top:`${n.y}%`}}><div className="node-icon">{n.icon==="core"?<Bot/>:n.icon==="host"?<Laptop/>:n.icon==="client"?(clientIsPhone?<Smartphone/>:<Laptop/>):n.icon==="project"?<FolderGit2/>:n.icon==="agent"?<BrainCircuit/>:<Server/>}</div><div className="node-label"><strong>{n.label}</strong><span>{n.sub}</span></div></div>)}
    <div className="map-readout readout-a"><span>HOST ADDRESS</span><strong>{ip}</strong><small>{dashboard?.network.adapters[0]?.link_speed || "LOCAL NETWORK"}</small></div>
    <div className="map-readout readout-b"><span>GPU</span><strong>{dashboard?.gpu.name || "NO TELEMETRY"}</strong><small>{dashboard?.gpu.temperature_c != null ? `${dashboard.gpu.temperature_c}°C / ${fmtPct(dashboard.gpu.utilization_percent)}` : "--"}</small></div>
    <div className="map-readout readout-c"><span>PERMISSIONS</span><strong>{dashboard?.runtime.permissions.approval_gated || 0} GUARDED</strong><small>{dashboard?.runtime.permissions.automatic || 0} AUTOMATIC</small></div>
    <div className="map-layers"><span>GRID <i/></span><span>SERVICES <i/></span><span>MODELS <i/></span><span>TOOLS <i/></span><span>PROJECTS <i/></span></div>
  </div>;
}

const PORTFOLIO_GUIDANCE: PortfolioOnboardingStep[] = [
  {
    key: "business",
    title: "Define your first business",
    complete: false,
    requires_human: true,
    description: "Capture its name, mission, stage, priorities, and the outcomes that matter now.",
  },
  {
    key: "systems",
    title: "Connect operating systems",
    complete: false,
    requires_human: true,
    description: "Start with read-only sources so CHIEF can build an evidence-backed picture.",
  },
  {
    key: "accounts",
    title: "Link financial accounts",
    complete: false,
    requires_human: true,
    description: "Authorize only the accounts and visibility needed for reliable business health.",
  },
  {
    key: "agents",
    title: "Assign agents and authority",
    complete: false,
    requires_human: true,
    description: "Define responsibilities, approval gates, budgets, and actions that remain human-only.",
  },
];

function PortfolioPanel({summary,onboarding,online,onRefresh,onBegin}:{summary?:PortfolioSummary;onboarding?:PortfolioOnboarding;online:boolean;onRefresh:()=>void;onBegin:()=>void}) {
  const counts = {
    businesses: boundedCount(summary?.businesses),
    agents: boundedCount(summary?.agents),
    systems: boundedCount(summary?.systems),
    accounts: boundedCount(summary?.financial_accounts),
  };
  const isBlank = summary?.is_blank ?? onboarding?.is_blank ?? Object.values(counts).every((count) => count === 0);
  const guidance = onboarding?.steps?.length ? onboarding.steps : PORTFOLIO_GUIDANCE;
  const completedCount = guidance.filter((step) => step.complete).length;
  const nextStep = onboarding?.next_step || guidance.find((step) => !step.complete)?.title || "Define your first business";
  const progress = guidance.length ? Math.min(100, Math.round((completedCount / guidance.length) * 100)) : 0;
  return <section className="portfolio-view hud-frame" aria-labelledby="portfolio-title">
    <header className="portfolio-header">
      <div><span className="eyebrow">FOUNDER OPERATING PORTFOLIO</span><h2 id="portfolio-title">PORTFOLIO</h2><p>A truthful view of the businesses, agents, systems, and financial accounts CHIEF is authorized to understand.</p></div>
      <div className={`portfolio-state ${isBlank ? "blank" : "active"}`}><i/><span>{isBlank ? "BLANK / INERT" : "PORTFOLIO REGISTERED"}</span></div>
    </header>

    <div className="portfolio-counts" aria-label="Portfolio summary">
      <PortfolioCount icon={<Building2/>} label="Businesses" value={counts.businesses} detail="Operating entities"/>
      <PortfolioCount icon={<UserRoundCog/>} label="Agents" value={counts.agents} detail={`${boundedCount(summary?.active_agents)} active · ${boundedCount(summary?.healthy_agents)} healthy`}/>
      <PortfolioCount icon={<Cable/>} label="Systems" value={counts.systems} detail={`${boundedCount(summary?.external_write_enabled_systems)} external-write enabled`}/>
      <PortfolioCount icon={<Landmark/>} label="Accounts" value={counts.accounts} detail="references only / no transaction authority"/>
    </div>

    <div className="portfolio-body">
      <section className="portfolio-blank-card">
        <div className="portfolio-orbit" aria-hidden="true"><span/><span/><BriefcaseBusiness/></div>
        <span className="eyebrow">{isBlank ? "NO PORTFOLIO DATA YET" : "PORTFOLIO FOUNDATION"}</span>
        <h3>{isBlank ? "Build the operating picture CHIEF should protect and grow." : "Your operating portfolio is taking shape."}</h3>
        <p>{isBlank ? "Nothing has been invented or preloaded. Add the first business deliberately, then connect only the people, systems, and accounts you choose." : "Continue onboarding to give CHIEF the context and authority boundaries needed for reliable execution."}</p>
        <div className="portfolio-next"><CircleDot/><div><span>NEXT RECOMMENDED STEP</span><strong>{humanizeStep(nextStep)}</strong></div></div>
        <div className="portfolio-actions">
          <button type="button" className="portfolio-primary" onClick={onBegin}><MessageSquare/> BEGIN WITH CHIEF</button>
          <button type="button" className="portfolio-secondary" onClick={onRefresh}><RefreshCw/> REFRESH SECURE DATA</button>
        </div>
        <small className="portfolio-integrity">{online ? summary ? "LIVE SUMMARY · NO SEEDED DATA" : "CORE CONNECTED · PORTFOLIO SUMMARY PENDING" : "CORE OFFLINE · COUNTS MAY BE STALE"}</small>
      </section>

      <section className="portfolio-onboarding" aria-labelledby="onboarding-title">
        <div className="portfolio-section-head"><div><span className="eyebrow">CONTROLLED ONBOARDING</span><h3 id="onboarding-title">FOUNDER SETUP PATH</h3></div><strong>{completedCount}/{guidance.length}</strong></div>
        <div className="portfolio-progress"><span style={{width:`${progress}%`}}/></div>
        <ol>{guidance.map((step,index)=>{
          const complete = step.complete;
          const current = !complete && index === Math.min(completedCount, guidance.length - 1);
          return <li key={step.key || `${step.title}-${index}`} className={complete ? "complete" : current ? "current" : "pending"}>
            <b>{String(index + 1).padStart(2,"0")}</b><div><strong>{humanizeStep(step.title || `Setup step ${index + 1}`)}</strong>{step.description && <p>{step.description}</p>}</div><span>{complete ? "COMPLETE" : current ? step.requires_human ? "HUMAN NEXT" : "NEXT" : "PENDING"}</span>
          </li>;
        })}</ol>
        <div className="portfolio-guardrail"><ShieldCheck/><p><strong>{onboarding?.ready_for_autonomy ? "Autonomy prerequisites reported ready." : "Least privilege from day one."}</strong> Connections begin disabled. External writes remain separately authorized and approval-gated.</p></div>
      </section>
    </div>
  </section>;
}

function PortfolioCount({icon,label,value,detail}:{icon:React.ReactNode;label:string;value:number;detail:string}) {
  return <article className="portfolio-count"><div className="portfolio-count-icon">{icon}</div><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></article>;
}

function boundedCount(value: number | undefined): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : 0;
}

function humanizeStep(value: string): string {
  const words = value.replace(/[_-]+/g," ").replace(/\s+/g," ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Define your first business";
}

function ChatPanel({messages,input,setInput,sendMessage,busy,voice,appInstalled,installReady,installApp}:{messages:ChatMessage[];input:string;setInput:(v:string)=>void;sendMessage:(e:FormEvent)=>void;busy:boolean;voice:BrowserVoiceControls;appInstalled:boolean;installReady:boolean;installApp:()=>Promise<void>}) {
  const audioActive = voice.listening || voice.speaking;
  return <section className="chat-panel hud-frame">
    <div className="c2-map-head"><div><span className="eyebrow">DIRECT CHANNEL</span><h2>CHIEF COMMAND INTERFACE</h2></div><div className="live-badge"><i/> {busy?"PROCESSING":voice.listening?"LISTENING":voice.speaking?"SPEAKING":"READY"}</div></div>
    <div className="chat-stream">{messages.map((m,i)=><article key={i} className={`message ${m.role}`}><div className="message-tag">{m.role==="user"?"OPERATOR":m.role.toUpperCase()}</div><p>{m.content}</p></article>)}</div>
    <div className={`voice-console ${audioActive ? "active" : ""}`}>
      <div className="voice-actions">
        <button type="button" className={voice.listening ? "voice-button listening" : "voice-button"} onClick={()=>voice.toggleListening(input)} disabled={!voice.inputAvailable || (busy && !voice.listening)} aria-pressed={voice.listening}>
          {voice.listening?<MicOff/>:<Mic/>}<span>{voice.listening?"STOP INPUT":"PUSH TO TALK"}</span>
        </button>
        <button type="button" className={voice.ttsEnabled ? "voice-button enabled" : "voice-button"} onClick={voice.toggleTts} disabled={!voice.outputAvailable} aria-pressed={voice.ttsEnabled}>
          {voice.ttsEnabled?<Volume2/>:<VolumeX/>}<span>{voice.ttsEnabled?"VOICE REPLIES ON":"VOICE REPLIES OFF"}</span>
        </button>
        <button type="button" className="voice-button stop" onClick={voice.stopAll} disabled={!audioActive}>
          <Square/><span>STOP AUDIO</span>
        </button>
        {installReady && !appInstalled && <button type="button" className="voice-button install" onClick={()=>void installApp()}><Download/><span>INSTALL APP</span></button>}
      </div>
      <div className="voice-disclosure" role="status" aria-live="polite">
        <strong className={`voice-status ${voice.status}`}>{voice.statusText}</strong>
        <small>{voice.privacyText}</small>
        <small className="pwa-state">APP: {appInstalled?"INSTALLED":installReady?"INSTALL READY":"INSTALL FROM BROWSER MENU"} · CAMERA: DISABLED</small>
      </div>
    </div>
    <form className="command-input" onSubmit={sendMessage}><TerminalSquare/><input value={input} onChange={e=>setInput(e.target.value)} placeholder="Issue directive to CHIEF..."/><button type="submit" disabled={busy}><Send/> TRANSMIT</button></form>
  </section>
}
function HeaderCell({label,value,sub,hot=false}:{label:string;value:string;sub?:string;hot?:boolean}){return <div className="header-cell"><span>{label}</span><strong className={hot?"teal":""}>{value}</strong>{sub&&<small>{sub}</small>}</div>}
function Panel({title,children,className=""}:{title:string;children:React.ReactNode;className?:string}){return <section className={`panel hud-frame ${className}`}><div className="panel-title"><span>{title}</span><i/></div>{children}</section>}
function Objective({name,status,primary}:{name:string;status:string;primary?:boolean}){return <div className={`objective ${primary?"primary":""}`}><span>{primary?"PRIMARY OBJECTIVE":"SECONDARY OBJECTIVE"}</span><strong>{name}</strong><small><i className="check"/> {status.toUpperCase()}</small></div>}
function Metric({label,value,pct}:{label:string;value:string;pct:number}){return <div className="metric"><div><span>{label}</span><strong>{value}</strong></div><div className="metric-track"><span style={{width:`${pct}%`}}/></div></div>}
function InfoRow({label,value}:{label:string;value:string}){return <div className="info-row"><span>{label}</span><strong>{value}</strong></div>}
function Log({time,text,warn=false}:{time:string;text:string;warn?:boolean}){return <div className="log-row"><code>[{time}]</code><span className={warn?"warn":""}>{text}</span></div>}
function Action({icon,label,onClick}:{icon:React.ReactNode;label:string;onClick?:()=>void}){return <button className="action-button" onClick={onClick}>{icon}<span>{label}</span></button>}
function GaugeBlock({icon,label,value}:{icon:React.ReactNode;label:string;value:string}){return <div className="gauge-block"><span>{icon}{label}</span><strong>{value}</strong></div>}
function Spark({seed}:{seed:number}){return <div className="spark">{Array.from({length:20},(_,i)=><i key={i} style={{height:`${10+((i*19+seed*13)%78)}%`}}/>)}</div>}
function Alert({text,mild=false}:{text:string;mild?:boolean}){return <div className={`alert ${mild?"mild":""}`}><AlertTriangle/><span>{text}</span></div>}
function Empty({text}:{text:string}){return <div className="empty-state">{text}</div>}
function ServiceRow({label,value,good=false}:{label:string;value:string;good?:boolean}){return <div className="service-row"><i className={good?"service-dot good":"service-dot"}/><span>{label}</span><strong>{value}</strong></div>}
function TaskRow({index,text,status}:{index:number;text:string;status:string}){return <div className="task-row"><b>{String(index).padStart(2,"0")}</b><div><strong>{text}</strong><i><span style={{width:`${34+index*11}%`}}/></i></div><small>{status}</small></div>}
function Timeline({online,ollama,sessions,executions}:{online:boolean;ollama:boolean;sessions:number;executions:number}){const rows=[{t:"CORE",v:online?"ONLINE":"OFFLINE",ok:online},{t:"MODEL",v:ollama?"READY":"OFFLINE",ok:ollama},{t:"SESSIONS",v:`${sessions} ACTIVE`,ok:sessions>0},{t:"EXECUTIONS",v:String(executions),ok:executions>0},{t:"TASKS",v:"STANDBY",ok:true}];return <div className="timeline"><div className="timeline-line"/>{rows.map((r,i)=><div key={r.t} className={`timeline-step ${r.ok?"done":"warn-step"}`}><i/><span>{r.t}</span><small>{r.v}</small></div>)}</div>}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
