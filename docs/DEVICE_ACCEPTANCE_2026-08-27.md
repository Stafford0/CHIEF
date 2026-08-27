# CHIEF + Ultron device acceptance — 2026-08-27

## Result

**PASS.** `feature/chief-ultron-conversation` completed local Ollama, live API, tool-boundary,
session-restart, and React interface acceptance on RamJet. The branch is ready for owner review
before merge; this report does not itself authorize or perform the merge.

## Device and runtime

- Host: RamJet, Windows 11, Intel Core i9-12900K, 32 GB RAM, NVIDIA RTX 3080 Ti
- Python: 3.12.10
- Node.js: 24.19.0
- Ollama: 0.33.1 at `127.0.0.1:11434`
- CHIEF model: `qwen3:4b`
- Ultron model: `llama3.1:8b`
- API: FastAPI/uvicorn on loopback port 8000
- UI: React/Vite on loopback port 5173

## Acceptance evidence

- `/health` returned online and `/ready` returned 200 with every state-store check true.
- `/ready` verified Ollama plus both configured model tags and reported the full `ready` state.
- Direct Ultron turns led with `llama3.1:8b`; CHIEF used `qwen3:4b`.
- Ultron truthfully reported zero tool access and the owner's final authority.
- CHIEF-led turns returned only CHIEF when Ultron had no distinct contribution.
- Requested shared turns rendered distinct, separately attributed CHIEF and Ultron messages.
- Shared turns streamed each attributed contribution as it completed, with visible per-agent
  progress and an operator cancellation control.
- Explicit owner instructions to silence Ultron for one turn or until rejoined were honored
  deterministically and persistent silence survived session-store reopening.
- `Ultron, rejoin` restored participation and streamed Ultron's direct response before CHIEF.
- Conversation state survived an API restart and retained speaker history.
- A natural `check system status` request executed the safe `system_status` tool automatically.
- A sensitive `run tests` request stopped at an exact five-minute approval and cancellation
  removed the proposal without execution.
- The React command center displayed active FastAPI/Ollama telemetry and separately styled
  OPERATOR, CHIEF, and ULTRON turns in the real browser UI.

Observed local response times varied from approximately 1.5 seconds for deterministic status
to 4–28 seconds for model-backed turns. Both models remained within the configured timeout.

## Defects found and closed during the run

1. Serialized `<think>` reasoning could reach visible CHIEF output. The Ollama adapter now
   removes complete and orphaned reasoning markers, with regression tests.
2. Ultron invented a fictional reason for lacking tools. Its prompt now requires the real
   architectural explanation.
3. Models could script or repeat the other speaker. Orchestration now retains only the
   attributed agent's contribution and suppresses exact echoes.
4. Ultron ignored an explicit owner silence instruction. That instruction is now enforced
   before the Ultron model is invoked.
5. Natural `check system status` wording missed the safe planner path. It now maps directly to
   the read-only runtime status tool.
6. Repository-root persona files were absent from built wheels. Both prompts are now packaged
   resources; a clean wheel installation loaded and verified both personas outside the repo.
7. A fixed browser timeout could expire before two sequential model calls completed. Chat now
   streams each contribution without a fixed whole-turn timeout and preserves cancellation.
8. Readiness did not verify Ollama or either configured model. It now reports `ready`,
   `degraded`, or `not_ready` and names each agent's availability.
9. Broad silence matching could mistake discussion for an instruction. Only explicit direct
   commands now alter Ultron participation, with regression coverage for analytical mentions.

## Final automated verification

- Python: 297 tests passed
- Ruff: all checks passed
- React/Vite: TypeScript and production build passed
- npm production dependency audit: zero known vulnerabilities
- Git whitespace validation: passed

One existing non-blocking warning remains: Starlette reports that its TestClient import path
for `httpx` is deprecated and recommends `httpx2`, which is already declared by this project.

## Scope not claimed

This run did not test microphone recognition, spoken replies, phone hardware, protected LAN
mode, TLS, or external integrations. Those capabilities are outside the CHIEF/Ultron local
conversation merge gate tested here.
