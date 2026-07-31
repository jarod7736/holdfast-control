# Architecture

This is a pull-based reconciliation system where devices run a local holdfastctl agent that inspects local state, generates a deterministic plan, applies only an exact approved plan, backs up managed files, rolls back safely, and reports status.

## Decision Summary

- Per-device pull agent: Every managed device runs a small, user-scoped `holdfastctl` agent
- Synology control plane: The Synology hosts the control API, report store, approval history, and dashboard
- No central SSH: Devices do not SSH into workstations or hold broad workstation credentials
- Git non-secret desired state: Non-secret manifests are stored in Git
- 1Password secret authority: Runtime-only secrets are resolved through 1Password
- Approval-gated apply: Changes must be approved before application
- Rollback capability: System supports safe rollback of changes

This approach ensures that no broad remote execution is possible from the control plane, with all configuration changes being reviewed and approved before application.

Reference: [Holdfast Control Implementation Plan](../2ndBrain/wiki/analyses/holdfast-control-implementation-plan.md)
