# CHIEF Command Center UI

CHIEF UI-001 is the first browser-based command interface for CHIEF.

## What works

- Live `/health` status
- Live `/system` identity and milestone data
- Functional `/chat` interface with session continuity
- Responsive desktop and mobile layout
- Automatic local-network API targeting
- Tactical command-center visual system
- Installable progressive web app shell
- Explicit push-to-talk browser transcription and opt-in spoken replies

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

Phone through an HTTPS endpoint or encrypted private tunnel:

```text
https://<CHIEF-PRIVATE-HOST>
```

The development frontend automatically targets port `8000` on the same hostname for the
CHIEF API. Plain remote HTTP is intentionally not allowed to transmit the pairing token; use
localhost for development or configure an HTTPS/private-tunnel endpoint for another device.
Protected LAN mode displays a pairing field for the 32+ character `CHIEF_API_TOKEN`. The
token is held in memory and browser `sessionStorage` for the current tab session only. It is
never embedded in the frontend build, placed in a URL, written to local storage, or cached by
the service worker. Use HTTPS or a private encrypted tunnel before entering a token remotely.

## Voice and privacy

- CHIEF never starts listening automatically.
- Push-to-talk transcription is placed in the draft for review; it is not sent automatically.
- Spoken replies are off by default and can be interrupted at any time.
- CHIEF does not record or retain microphone audio in the UI.
- Browser speech services may process audio locally or in the cloud, depending on the browser and operating system.
- Microphone input requires HTTPS or localhost and explicit browser permission.
- Camera access remains disabled.

## Install and offline behavior

Build and preview the production app to register its service worker:

```powershell
npm run build
npm run preview
```

The offline cache contains only the static application shell. Conversations, API responses, approvals, and telemetry are never cached. Offline mode cannot send commands to CHIEF Core.

## Optional API override

Create `.env.local` inside `apps/chief-ui`:

```text
VITE_CHIEF_API_URL=http://127.0.0.1:8000
```

## UI-001 scope

This milestone intentionally keeps the center of the interface focused on direct interaction with CHIEF. Later milestones can replace placeholder panels with live system telemetry, tools, permissions, memory, projects, alerts, and execution activity.
