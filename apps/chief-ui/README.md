# CHIEF Command Center UI

CHIEF UI-001 is the first browser-based command interface for CHIEF.

## What works

- Live `/health` status
- Live `/system` identity and milestone data
- Functional `/chat` interface with session continuity
- Responsive desktop and mobile layout
- Automatic local-network API targeting
- Tactical command-center visual system

## Run locally

Start CHIEF Core from the repository root:

```powershell
uvicorn chief.core.app:app --reload --host 0.0.0.0
```

In a second PowerShell window:

```powershell
cd apps\chief-ui
npm install
npm run dev
```

Desktop:

```text
http://127.0.0.1:5173
```

Phone on the same network:

```text
http://<CHIEF-PC-IP>:5173
```

For example, if the CHIEF PC is `192.168.12.137`, open:

```text
http://192.168.12.137:5173
```

The frontend automatically targets port `8000` on the same hostname for the CHIEF API.

## Optional API override

Create `.env.local` inside `apps/chief-ui`:

```text
VITE_CHIEF_API_URL=http://127.0.0.1:8000
```

## UI-001 scope

This milestone intentionally keeps the center of the interface focused on direct interaction with CHIEF. Later milestones can replace placeholder panels with live system telemetry, tools, permissions, memory, projects, alerts, and execution activity.
