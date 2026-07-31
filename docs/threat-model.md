# Threat Model

## Assets

- **Secrets**: Runtime secrets resolved through 1Password
- **Device Configs**: Non-secret desired state manifests
- **Control Tokens**: Report-only tokens for device reporting

## Actors

- **Human Users**: Authorized to approve changes, view status, and access dashboard
- **Device Agents**: Local holdfastctl agents that inspect, plan, and apply changes
- **Control Plane**: Synology server that handles API, reports, approvals, and dashboard

## Trust Boundaries

- **Git/Manifests**: Non-secret desired state stored in Git repository
- **Control Plane**: Synology server that handles API and dashboard
- **Device Agent**: Local agent running on each device
- **1Password**: Secret resolution authority

## Key Threats and Mitigations

- **Secret Leakage in Reports**: Reports are redacted to prevent secrets from appearing in logs or database
- **Stolen Report Token Scope-limited**: Report tokens are read-only and cannot approve changes or retrieve secrets
- **Malicious Manifest Rejection**: Schema validation rejects any manifest with literal secrets or unsafe commands
- **TOCTOU Apply Race**: Handled by re-inspection and hash binding before apply - changes must be re-approved if state has changed

This design ensures that even if one component is compromised, the overall security is maintained through isolation and validation.
