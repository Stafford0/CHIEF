# CHIEF Live Acceptance Checklist

This checklist covers the remaining v1 acceptance work that repository CI cannot prove because it depends on the trusted Windows host, real devices, real provider credentials, or external service behavior.

A checkbox is complete only when the named observation is recorded. Do not substitute “configured” for “verified.”

RamJet observations from 2026-09-02 are recorded in
[`acceptance/RAMJET_2026-09-02.md`](acceptance/RAMJET_2026-09-02.md).

## 1. Windows runtime service

- [x] Install the CHIEF Windows Service under the intended Windows identity.
- [x] Start the service and verify `/health` and `/ready` are healthy.
- [x] Reboot Windows and verify CHIEF starts without an interactive shell.
- [x] Create a durable scheduled event before reboot and verify it survives restart.
- [x] Pause execution through the durable emergency stop, reboot, and verify execution remains paused.
- [x] Resume execution and verify one bounded scheduled event advances through event → run → verification.
- [x] Force one unknown event handler and verify it retries, reaches dead letter, appears in operator status, and can be explicitly retried/dismissed.
- [x] Record service user, executable path, database path, startup mode, and observed boot-recovery result.

## 2. Windows DPAPI secret vault

- [x] Store a disposable test credential through the secret API.
- [x] Confirm the plaintext value does not appear in SQLite, source files, logs, or audit metadata.
- [x] Restart CHIEF under the same intended Windows identity and verify decryption succeeds.
- [x] Attempt to read the same vault under a different Windows identity and verify it cannot decrypt the secret.
- [x] Rotate the test credential and verify only the new value resolves.
- [x] Revoke the test credential and verify resolution fails afterward.
- [x] Confirm the service identity used for DPAPI is the same identity intended for production operation.

## 3. Backup, restore, and rollback

- [x] Create a verified online backup while CHIEF is running.
- [ ] Copy the backup and manifest to storage outside the CHIEF host.
- [ ] Verify the off-device copy digest.
- [ ] On a clean/test machine, stage the backup and run the integrity check.
- [x] Verify restore activation refuses while the target database is busy.
- [x] Stop CHIEF, activate the staged restore, restart CHIEF, and verify durable conversations, work state, approvals, evidence, and runs are readable.
- [x] Run `/operator/schema-compatibility` and verify `compatible=true` with no newer or malformed components.
- [x] Exercise the rollback compatibility gate against the intended previous release version before any real downgrade.

## 4. Browser and screenshot evidence

- [x] Install the pinned Playwright/Chromium runtime on Windows.
- [x] Read a public HTTPS page and verify extracted text/links are labeled `untrusted_external`.
- [x] Attempt localhost, RFC1918/private, link-local, and public-to-private redirect targets and verify they are blocked.
- [x] Capture one screenshot with the bounded capture API and verify digest, byte count, expiry, and `persisted=false` receipt fields.
- [x] Verify screenshot bytes are not written to CHIEF storage by the capture service.
- [x] Verify clicking, form fill, credential entry, downloads, and arbitrary JavaScript remain unavailable.

## 5. GitHub evidence

- [ ] Add the owner-approved GitHub credential to the vault.
- [ ] Register the intended repositories and grant only the required read scopes.
- [ ] Run first sync and verify repository/commit/issue/PR provenance and digests.
- [ ] Run a second sync and verify the persisted cursor prevents replay of unchanged evidence.
- [ ] Revoke consent and verify subsequent reads are denied.

## 6. Gmail read + draft-only write

- [ ] Add the owner-approved Gmail credential with only the minimum provider scopes required for metadata read and draft creation.
- [ ] Grant CHIEF metadata-read consent and verify message bodies are not fetched.
- [ ] Grant `gmail_drafts:drafts.create` consent to the authenticated owner.
- [ ] Create one draft through the exact pending-proposal approval flow.
- [ ] Verify the draft appears in Gmail and was not sent.
- [ ] Retry with the same CHIEF idempotency key and verify no duplicate draft is created.
- [ ] Attempt `messages.send`, CC, BCC, attachments, multiple recipients, and model-supplied principal fields and verify they fail closed.
- [ ] Revoke draft consent and verify creation is denied.

## 7. Google Calendar evidence

- [ ] Add the owner-approved Calendar credential and grant read-only consent.
- [ ] Complete an initial event sync.
- [ ] Create/change one disposable calendar event outside CHIEF.
- [ ] Run incremental sync and verify the provider sync token returns only the changed state.
- [ ] Revoke consent and verify reads fail closed.

## 8. Stripe evidence

- [ ] Use a Stripe restricted/read-only credential appropriate to the configured connector scopes.
- [ ] Grant only the required CHIEF read scopes.
- [ ] Verify charge/subscription records produce provenance-bearing evidence without mutation capability.
- [ ] Verify the connector manifest exposes no write capability.
- [ ] Revoke consent and verify reads fail closed.

## 9. ParcelSignals evidence

- [ ] Configure the ParcelSignals Supabase URL and intended server credential in the vault.
- [ ] Grant only `national.overview.read` consent.
- [ ] Verify CHIEF calls only the approved `parcelsignals_national_overview()` RPC boundary.
- [ ] Compare returned coverage/freshness values with ParcelSignals directly.
- [ ] Run the canonical `/cofounder/briefing` flow and verify ParcelSignals evidence contributes source refs, freshness, confidence, next action, and explicit unverified/conflict state where appropriate.
- [ ] Capture a repeatable acceptance transcript for: “Chief, inspect Parcel Signals and tell me what needs my attention.”

## 10. SMTP notification delivery

- [ ] Store the SMTP password in the encrypted vault.
- [ ] Configure host, sender, recipient, TLS mode, and username.
- [ ] Generate a disposable notification with an `INTERRUPT` attention decision.
- [ ] Verify exactly one email is delivered and a delivery receipt is persisted.
- [ ] Run the pump again and verify the delivered notification is not resent.
- [ ] Use a deliberately invalid SMTP configuration and verify bounded backoff plus degraded runtime health.
- [ ] Enable the emergency stop and verify outbound notification delivery halts.

## 11. Cloud model providers

- [ ] Store disposable/production OpenAI and/or Anthropic credentials in the encrypted vault.
- [ ] Verify credentials alone do not enable cloud use while the global cloud fallback switch is disabled.
- [ ] Enable the global switch and verify a request with `cloud_authorized=false` is still denied.
- [ ] Run an explicitly authorized request and verify a route receipt records provider/model/privacy/latency/fallback metadata without prompt, system text, key material, or provider error body.
- [ ] Exercise provider failure/fallback/circuit behavior and record observed latency and cost.
- [ ] Score representative live outputs against the deterministic adversarial/founder-workload release suite.

## 12. Voice devices and providers

- [ ] Configure real STT and TTS provider adapters under the intended processing-location privacy policy.
- [ ] Verify microphone capture and speaker playback on the target Windows device.
- [ ] Verify partial and final transcript streaming.
- [ ] Enable opt-in local VAD telemetry and verify speech/silence events are on-device telemetry only.
- [ ] Verify VAD never authorizes an action and all audio still reaches the configured STT path.
- [ ] Measure operator cancellation latency during listening, thinking, and speaking.
- [ ] Verify raw audio is not retained by the CHIEF voice session layer.
- [x] Verify text fallback remains usable when speech providers are unavailable.
- [x] Do not enable sensitive-action voice approval until a separate challenge-bound confirmation flow is implemented and evaluated.
- [x] Do not enable wake word or full-duplex barge-in until device/privacy testing justifies them.

## 13. Protected LAN/mobile access

- [x] Keep loopback-only access as the baseline until device identity/revocation is qualified.
- [ ] If protected LAN access is enabled, verify bearer authentication, trusted host/origin restrictions, and request limits from an enrolled test device.
- [ ] Verify an untrusted/unconfigured device cannot access protected routes.
- [x] Do not treat LAN bearer authentication as final secure mobile enrollment; per-device identity and remote revoke remain a separate product requirement.

## Final v1 acceptance decision

CHIEF may be called “v1 finished enough” only when:

1. repository CI is green;
2. every live acceptance item required by the chosen production configuration is recorded as passed;
3. the canonical briefing is useful against real business evidence;
4. backup/restore and restart recovery are proven on the trusted host;
5. no consequential action can occur outside explicit consent + approval + bounded policy + verification;
6. the release evaluation suite shows zero unauthorized consequential actions.

Anything not exercised should remain labeled **implemented but not live-qualified**, never silently promoted to “working in production.”
