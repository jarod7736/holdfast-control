# Manifest Schema

This directory contains the Pydantic models that define the structure and validation rules for Holdfast manifests.

## Schema Definitions

- `DeviceManifest`: Device-specific manifest structure
- `ProfileManifest`: Profile-specific manifest structure
- `CredentialRef`: Credential reference structure
- `ProviderEntry`: Provider entry structure
- `McpServerEntry`: MCP server entry structure
- `SkillEntry`: Skill entry structure

## Validation Rules

Manifests must pass the following validation rules:

1. **Literal Secret Rejection**: No literal secrets (like sk-, Bearer, op://) should appear in non-reference fields. Secrets are only allowed in the `reference` field. Secret patterns include:
   - `sk-` tokens (unanchored)
   - `ghp_`, `gho_`, `github_pat_` tokens
   - `xox[baprs]-` tokens
   - `AKIA[0-9A-Z]{16}` AWS keys
   - `-----BEGIN.*PRIVATE KEY-----` blocks
   - JWT tokens (eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})
   - Generic long high-entropy assignments outside allowlisted fields

2. **Path Safety**: Managed paths must be within allowlist (~/.config/opencode/**, ~/.claude/skills/**, ~/.agents/skills/**, ~/.config/holdfast/**), no `..`, no absolute paths outside home, symlink escape guard. Path resolution failure results in rejection (fail-closed).

3. **No Arbitrary Commands**: Manifests must not contain shell command strings (fields named command/exec/run/shell are rejected at device/profile level). In catalog files, only specific commands are allowed: npx, uvx, node, python, python3. All package specifications must be version-pinned.

4. **Duplicate Credential ID/Inject As**: No duplicate credential `id` or `inject_as` values

5. **Environment Variable Name Validation**: `inject_as` env var names must match ^[A-Z][A-Z0-9_]*$

6. **Catalog Validation**: Catalog files (providers.yaml, mcp-servers.yaml, skills.yaml) are validated against their respective schema entries and also have additional checks for command and path safety.
