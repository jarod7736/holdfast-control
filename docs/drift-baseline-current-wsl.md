# Drift Baseline — current-wsl (2026-07-30)

Source: read-only inventory of the pilot workstation (this machine).

## Managed (to be owned by holdfast-control)
- `~/.config/opencode/opencode.json` — providers (lemonade/amd-halo), MCP (github, halo_commander, nas_files via LiteLLM gateway), plugins (opentmux, opencode-agent-tmux, oh-my-opencode-slim), explore/general agents disabled. **Inline bearer key removed 2026-07-30 → `{env:LITELLM_API_KEY}`.**
- `~/.config/opencode/oh-my-opencode-slim.json` — presets: lemonade, openai, opencode-go.
- `~/.config/opencode/skills/` — 8 managed skills (clonedeps, codemap, deepwork, oh-my-opencode-slim, reflect, simplify, verification-planning, worktrees).
- `~/.config/opencode/{agent,agents,command,commands}/` — present, inventoried.
- opencode binary 1.18.10 at `~/.opencode/bin/opencode`.

## Unmanaged-by-design (reported as drift, never modified)
- `~/.zshrc` env injection: `LEMONADE_API_KEY`, `LITELLM_API_KEY` via `op` (plaintext never in file).
- `~/.agents/skills/` (38 ad-hoc skill dirs), `~/.claude/skills/` (12).
- Project-level configs, e.g. `~/projects/loop_graph_demo/opencode.json`.

## Credential state after rotation (2026-07-30)
- Old inline LiteLLM key: **revoked** (401 confirmed).
- New virtual key `opencode-jarod-laptop` scoped to MCP servers github/halo_commander/nas_files; value only in 1Password item `opencode-litellm-jarod-laptop-api-key`; verified 200 on `/v1/mcp/server`.
- `~/.config/opencode/opencode.json.bak-holdfast-rotation` contains the revoked key — safe (dead credential), delete after Phase 2 pilot confirmed.

## LAN-Orangutan (discovered 2026-07-30 via Synology SSH)
- Container `lan-orangutan`, image `ghcr.io/291-group/lan-orangutan:latest`, healthy, host networking, port 291, restart unless-stopped.
- LAN device-discovery dashboard (nmap-based); data at `/volume1/docker/lan-orangutan/data`; password not yet set.
- URL: http://192.168.1.181:291. Role in Holdfast Control: read-only device-discovery adapter (Phase 3).
