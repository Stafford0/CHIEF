# CHIEF and Ultron Conversation Architecture

CHIEF and Ultron share one user interface and one visible conversation. Every rendered
message is structurally attributed to `USER`, `CHIEF`, or `ULTRON`.

CHIEF is the primary operator. It receives relevant CHIEF memory, may use approved tools,
and can act autonomously within standing permissions. The user remains the final authority,
and consequential actions retain explicit approval gates.

Ultron is a separate local Ollama voice with its own Markdown system prompt. It receives the
visible conversation but never receives CHIEF's retrieved private memory, tool registry, or
execution path. It evaluates every exchange and can return `[[SILENT]]` when it has nothing
useful to add. When the user begins a message by addressing Ultron, Ultron leads and CHIEF
may join if operationally useful. Ultron can recommend actions but cannot execute them.

## Visual scope decision

The previously discussed Three.js Ultron figure viewer was intentionally discarded on
August 27, 2026. It is not a missing deliverable and should not be recovered or rebuilt unless
the owner explicitly reopens the decision. Ultron remains a separately attributed voice in
the shared CHIEF conversation UI.

## RamJet model selection

Benchmarked on August 27, 2026 against the locally installed models:

| Model | Single-response result | Decision |
| --- | --- | --- |
| `qwen3:4b` | Fast, but consumed the answer budget with internal reasoning | Retain as current CHIEF default pending a separate CHIEF benchmark |
| `llama3.1:8b` | Complete direct answer in about 5.3 seconds | Selected for Ultron |
| `qwen3.6:latest` | Timed out after 180 seconds | Rejected for interactive use |

With `qwen3:4b` and `llama3.1:8b` generating concurrently, Ultron completed in about 10.8
seconds. This is acceptable for the initial local release and should be rechecked after any
hardware, quantization, context-window, or Ollama configuration change.
