"""Inbound mode: the control plane dials the node, instead of the reverse.

Default remote-worker mode is outbound — the node dials the control plane, which
is what a fleet needs (goal_v2.md B2/B5.2) and what works behind NAT with no
open ports. This package is the opposite arrangement, for two cases outbound
cannot serve:

  * the node is reachable but the panel is not (the panel is the laptop);
  * several people want to share one GPU box.

The second is the reason this exists. Outbound is structurally 1:1 — a worker
process holds exactly one endpoint, one pinned certificate and one worker id
(``agent.py``), so "let a colleague use the 4090" means SSHing into the box,
repointing it and restarting, which disconnects whoever had it. Inbound is 1:N
by construction: the node listens once and any panel holding a key connects,
concurrently, without shell access to the machine.

See ``docs/adr/inbound-node-mode.md`` for the security posture, which is
deliberately weaker than outbound's and is scoped to LAN / self-hosted use.
"""
