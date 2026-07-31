# Credential Policy

## Vault Split

- **holdfast-lan**: Human-managed infrastructure credentials
- **holdfast-automation**: Narrowly scoped unattended identities for control plane, Authentik, and deployment automation

The control plane must not receive a broad read/write service account for `holdfast-lan`.

## Workstation Access

Workstations use the interactive 1Password app, CLI, and SSH agent, with no long-lived service-account token. Servers use narrowly scoped, read-only service accounts by default.

## Runtime Environment Isolation

Unavoidable materialized runtime files require restricted ownership and mode `0600`. Runtime environment is explicit: a provider variable inherited from the shell but absent from the device manifest is reported as unmanaged drift and excluded from the launcher environment.

## Metadata Fields

- `owner`: Owner of the credential
- `purpose`: Purpose of the credential
- `environment`: Environment (dev, prod, etc.)
- `device`: Device this credential is for
- `service`: Service the credential is for
- `created_at`: When the credential was created
- `rotated_at`: When the credential was last rotated
- `rotation_days`: How often to rotate
- `managed_by`: Who manages this credential
- `credential_type`: Type of credential
- `recovery_required`: Whether recovery is required

## Security Principle

**NEVER store resolved secrets anywhere** - all resolved secrets are ephemeral and only used at runtime.

Reference: [Holdfast Control Implementation Plan](../2ndBrain/wiki/analyses/holdfast-control-implementation-plan.md)
