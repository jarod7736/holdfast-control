# Fleet Audit - 2026-08-07

Triggered by onboarding `JAROD-DESKTOP`. The install surfaced one landmine in
`scripts/install-agent.sh` and three defects in `scripts/fleet_check.sh`, two of
which were producing wrong answers about device health.

## Fleet state after this audit

| Device | API freshness | Host check | Note |
|---|---|---|---|
| `JAROD-DESKTOP` | PASS | PASS | Newly enrolled. Previously SKIP (no SSH mapping). |
| `amd-halo` | PASS | PASS | First time this was genuinely checked, see defect 3. |
| `jarod7736-laptop` | FAIL (stale) | unreachable | Expected: it is a laptop and is often powered off. |
| `lobsterboy` | PASS | PASS | Was reported broken. It never was, see defect 2. |

`fleet_check.sh` still exits `SOME CHECKS FAILED`, solely because the laptop is
off. Read the per-device lines, not the exit status.

## `scripts/fleet_check.sh` defects (fixed 2026-08-07)

**1. Swallowed SSH errors reported as specific health failures.** The host check
wrapped `subprocess.run(['ssh', ...])` in `except: pass`, then fell through to
`print('FAIL: holdfastctl-agent.timer is not active')` whenever the result was
missing or non-zero. A host that could not be reached at all was reported as a
host whose timer was down and whose config had drifted: two confident, specific,
false diagnoses. This is what made defect 2 look like a real incident.

Fixed with an `ssh_run()` helper that separates transport failure (ssh exit 255)
from check failure, printing `ERROR: could not reach <host> - <reason>` instead
of inventing a result.

**2. Wrong SSH alias for `lobsterboy`.** The script mapped device `lobsterboy` to
SSH host `lobsterboy-local`, which does not resolve. `~/.ssh/config` defines the
alias as `lobsterboy` (`lobsterboy.tail1c66ec.ts.net`). Combined with defect 1
this produced two fabricated failures on a healthy device. Verified by hand:
timer `active`, config already on the tailnet URL. Aliases now live in an
`SSH_HOSTS` dict.

**3. `amd-halo` hardcoded as the local machine.** The script assumed
`device_id == 'amd-halo'` meant "run the checks locally", which is only true when
the script runs on `amd-halo`. Run from anywhere else it reported the *local*
machine's timer and config under `amd-halo`'s name. From `JAROD-DESKTOP` this was
a false PASS, the more dangerous direction: a device that had never been checked
appeared green.

Fixed by reading `device_id` from the local `~/.config/holdfastctl/config.yaml`
and using the local path only for that device. The script is now correct
regardless of which machine it runs from.

Note `amd-halo` is reached over Tailscale SSH, which can require periodic
re-authentication. When that lapses the check now fails honestly as
`ERROR: could not reach` rather than as a fake config failure.

## `scripts/install-agent.sh` landmines (not fixed)

**Stale default control plane.** `CONTROL_PLANE_URL` still defaults to
`http://127.0.0.1:8000`, predating the tailnet cutover in commit 7674de1. The
agent config is written once and never overwritten, so any install that omits
`--control-plane` permanently bakes in a dead endpoint, which `fleet_check.sh`
then reports as config drift. Tracked in `TODO.md`.

**Interpreter mismatch on WSL dev boxes.** The script calls bare `pip3`, which on
`JAROD-DESKTOP` resolves through `~/.pyenv/shims/` to Python 3.8 while the package
declares `requires-python = ">=3.12"`. The install fails with
`requires-python: 3.8.10 not in '>=3.12'`. Working invocation:

```sh
PYENV_VERSION=3.12.0 scripts/install-agent.sh --control-plane https://holdfast.tail1c66ec.ts.net
```

Do not substitute a `.venv` install: `templates/systemd/holdfastctl-agent.service`
hardcodes `ExecStart=%h/.local/bin/holdfastctl`, so it must be a
`pip install --user`. The resulting console script gets an absolute shebang, so
systemd runs it with no environment variable set.

## Operational notes

A *scoped* enrollment makes `holdfastctl report --enrollment-code` print the
LiteLLM virtual key exactly once, and it is never stored. Run that step where the
output will not be captured, and file the key in 1Password immediately. The
admin token for minting codes is at
`op://holdfast-automation/holdfast-control admin token/credential`; reaching that
vault requires the service-account token in `~/.config/op/service-account.env`.

`JAROD-DESKTOP` is a distinct device from `manifests/devices/jarod7736-laptop.yaml`
and has no manifest of its own, so `holdfastctl reconcile` against it needs an
explicit `--manifest`.

## Verifying a device

```sh
holdfastctl report          # exit 0 and "Reported <id> to <url>" means enrolled
scripts/fleet_check.sh      # per-device API and host checks
```
