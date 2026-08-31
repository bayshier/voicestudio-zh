# Inbound node mode: the panel dials the GPU machine

**Status:** accepted
**Date:** 2026-08-11

## Context

Remote workers connect *outbound*: the node dials the control plane, presents a
single-use enrollment token, pins the control plane's certificate on first use,
and proves possession of an Ed25519 key on every reconnect. That is the right
default. It works behind NAT with no open ports, it is what a hosted fleet
needs (`remote/goal_v2.md` B2), and its security posture is strong.

It is also structurally **1:1**. A worker process holds exactly one endpoint,
one pinned certificate and one worker id (`backend/worker/agent.py`). So when a
second person wants to use the same GPU box, the only route is:

1. get shell access to the machine — someone else's machine;
2. mint an enrollment token on *their* panel;
3. edit the start script to point at their address;
4. restart, which **disconnects whoever was using it**.

Sharing a GPU therefore requires root on it and evicts the incumbent. For a
household or a small team with one 4090, that is the difference between a
feature and a thing nobody uses. No amount of polish on the outbound flow fixes
it, because the constraint is the shape of the connection, not the UI.

## Decision

Add an **inbound** mode, alongside outbound, in which the node listens and any
panel holding an API key connects to it. Outbound remains the default and is
unchanged.

Specifics:

- **New gRPC service `NodeService`, hosted by the node** — `Attach`,
  `FetchResult`, `PushInput` — mirroring `WorkerService`. **Transport roles
  invert; message roles do not.** The node still sends `WorkerMessage`
  (heartbeats, capabilities, progress, results) and the panel still sends
  `ServerMessage` (assignments, cancels, acks), so every state machine on both
  sides is untouched. `Register` folds into the stream as the first exchange,
  reusing `RegisterRequest`/`RegisterResponse` verbatim rather than defining
  parallel messages.
- **Per-panel API keys, not one node key.** Stored as SHA-256 hashes with a
  constant-time compare; the plaintext exists once, in the issuing response.
- **Failed-auth throttle**, per source address, so one stale bookmark cannot
  lock out a different panel.
- **Concurrent panels are allowed.** Two panels may each believe they hold the
  node's free slot and both dispatch; the worker's `WORKER_AT_CAPACITY` reject
  is authoritative (`goal_v2.md` B5.5) and the loser retries. Contention
  degrades to a queue, not to corruption. Serialising panels instead would
  recreate the eviction problem in a nicer wrapper.
- **Ed25519 identity is retained.** The API key admits a *panel to the node*;
  the node still proves itself to the panel with its keypair. The key is
  admission, never identity.
- **A visible connection log with a kick action** replaces per-job approval.
- **Default bind `127.0.0.1`.** Listening on other interfaces is a separate,
  explicit setting, and the bound address is shown in the UI.
- **Off by default.** Enabling it is the consent surface.

## Transport security

Inbound connections always use TLS. The node creates a persistent self-signed
certificate, and each connection string carries its SHA-256 fingerprint. The
panel performs a discovery handshake only to retrieve the leaf certificate,
verifies the fingerprint before sending credentials or user data, and then
opens gRPC with that pinned certificate as its sole trust root. Certificate and
hostname verification therefore remain mandatory without requiring a public
certificate authority or a separate manual pinning step.

There is no plaintext fallback. The listener cannot open any port, including a
non-loopback port, without TLS credentials. A fingerprint mismatch fails closed
before the API key, reference audio, jobs, or rendered audio are sent.

The API key remains admission rather than identity: the connection string is a
private bearer credential, while the certificate authenticates the GPU machine.
The certificate persists across restarts. Replacing it invalidates old
connection strings, which must be reissued with the new fingerprint.

This mode remains for locally owned hardware, not the hosted fleet.
`goal_v2.md` B2 and B5.2 require hosted workers to dial out with no inbound
ports; nothing here relaxes that, and the hosted platform must not inherit this
path. See "Amendment" below.

## Amendment to goal_v2.md B5.2

B5.2 reads "Workers connect outbound; the control plane never dials in", and
B2 promises "no static IP, no inbound ports". Those remain true **for the
hosted fleet**, which is what they were written about. They are now scoped
statements rather than global ones: the OSS desktop product also supports an
inbound mode for locally-owned hardware, with pinned TLS as documented above.
The conformance fixtures for the Go control plane must cover
`WorkerService` only.

## Alternatives rejected

- **Multi-endpoint dial-out** (the node dials N control planes). Preserves the
  outbound principle and solves nothing: the second user still needs SSH access
  to add their endpoint and still needs a token from their own panel. The
  complexity being removed is "you need shell access to someone else's GPU
  box", and this keeps all of it.
- **One shared node key.** Cheaper, and revoking it kicks everyone and forces a
  re-paste on every machine — so in practice nobody revokes, and the credential
  outlives the reason it was issued. A shared key also leaves no record of who
  used it, which makes the connection log much less useful.
- **Per-job approval prompts.** Makes a shared GPU unusable and trains people
  to click yes. Visibility plus a kick button is the better trade.
- **Reusing `WorkerService` with the panel as gRPC client.** Does not work: the
  gRPC client sends the request-stream type, so the panel would be sending
  `WorkerMessage`. Mirroring the service is what keeps the message roles — and
  therefore every state machine — unchanged.
