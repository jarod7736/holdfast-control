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

1. **Literal Secret Rejection**: No literal secrets (like sk-, Bearer, op://) should appear in non-reference fields
2. **Path Safety**: Managed paths must be within allowlist (~/.config/opencode/**, ~/.claude/skills/**, ~/.agents/skills/**, ~/.config/holdfast/**), no `..`, no absolute paths outside home, symlink escape guard
3. **No Arbitrary Commands**: Manifests must not contain shell command strings (fields named command/exec/run/shell are rejected at device/profile level)
4. **Duplicate Credential ID/Inject As**: No duplicate credential `id` or `inject_as` values
5. **Environment Variable Name Validation**: `inject_as` env var names must match ^[A-Z][A-Z0-9_]*$
