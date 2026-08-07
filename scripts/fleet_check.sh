#!/usr/bin/env bash
set -euo pipefail

# Load 1Password service account
set -a
source ~/.config/op/service-account.env
set +a

# Get admin token from 1Password
ADMIN="$(op read 'op://holdfast-automation/holdfast-control admin token/credential' | tr -d '\n')"

# Fetch devices from API
DEVICES_JSON=$(curl -s --max-time 10 -H "Authorization: Bearer $ADMIN" https://holdfast.tail1c66ec.ts.net/api/v1/devices)

# Parse devices using Python
python3 -c "
import json
import sys
import time
import os
import subprocess

CONFIG_PATH = os.path.expanduser('~/.config/holdfastctl/config.yaml')
TAILNET_LINE = 'control_plane_url: https://holdfast.tail1c66ec.ts.net'

# SSH aliases as defined in ~/.ssh/config.
SSH_HOSTS = {
    'amd-halo': 'amd-halo',
    'jarod7736-laptop': 'jarod7736-laptop',
    'lobsterboy': 'lobsterboy',
}

def local_device_id():
    # Which device is this machine? Decides local-vs-ssh below, so the script
    # stays correct wherever it is run from.
    try:
        with open(CONFIG_PATH, 'r') as f:
            for line in f:
                if line.startswith('device_id:'):
                    return line.split(':', 1)[1].strip()
    except Exception:
        pass
    return None

def ssh_run(host, remote_cmd):
    # Returns (ok, stdout, unreachable). A non-empty 'unreachable' means ssh
    # itself failed, which is not the same as the check failing.
    try:
        r = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', host, remote_cmd],
                           capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return (False, '', 'ssh timed out')
    except Exception as e:
        return (False, '', 'ssh error: {}'.format(e))
    if r.returncode == 255:
        detail = r.stderr.strip().splitlines()
        return (False, '', detail[-1] if detail else 'unreachable')
    return (r.returncode == 0, r.stdout, '')

devices = json.loads(sys.argv[1])
now = int(time.time())
all_passed = True
THIS_DEVICE = local_device_id()

# Process each device
for device in devices:
    device_id = device.get('id', 'unknown')
    print(f'API Check for device {device_id}:')
    
    # Check last_reported_at freshness (must be within 900 seconds)
    last_reported = device.get('last_reported_at')
    if last_reported is None:
        print('  FAIL: last_reported_at is missing')
        all_passed = False
    else:
        last_reported = int(last_reported)
        if now - last_reported > 900:
            print(f'  FAIL: last_reported_at ({last_reported}) is older than 900 seconds')
            all_passed = False
        else:
            print(f'  PASS: last_reported_at ({last_reported}) is fresh')
    
    # Check drift_status presence
    drift_status = device.get('drift_status')
    if drift_status is None:
        print('  FAIL: drift_status is missing')
        all_passed = False
    else:
        print(f'  PASS: drift_status ({drift_status}) is present')
    
    print(f'Host Check for device {device_id}:')
    
    host = SSH_HOSTS.get(device_id)

    if device_id == THIS_DEVICE:
        # This machine - run commands directly, no ssh.
        try:
            result = subprocess.run(['systemctl', '--user', 'is-active', 'holdfastctl-agent.timer'],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and 'active' in result.stdout:
                print('  PASS: holdfastctl-agent.timer is active')
            else:
                print('  FAIL: holdfastctl-agent.timer is not active')
                all_passed = False
        except Exception as e:
            print(f'  ERROR: could not check local timer - {e}')
            all_passed = False

        try:
            with open(CONFIG_PATH, 'r') as f:
                if TAILNET_LINE in f.read():
                    print('  PASS: config points at Tailscale URL')
                else:
                    print('  FAIL: config does not point at Tailscale URL')
                    all_passed = False
        except Exception as e:
            print(f'  ERROR: could not read local config - {e}')
            all_passed = False
    elif host is None:
        print('  SKIP: No SSH mapping for this device')
    else:
        ok, out, unreachable = ssh_run(host, 'systemctl --user is-active holdfastctl-agent.timer')
        if unreachable:
            # Do not claim the timer is down when we never got to look at it.
            print(f'  ERROR: could not reach {host} - {unreachable}')
            all_passed = False
        else:
            if ok and 'active' in out:
                print('  PASS: holdfastctl-agent.timer is active')
            else:
                print('  FAIL: holdfastctl-agent.timer is not active')
                all_passed = False

            # Remote command must be a SINGLE ssh argument - ssh joins multiple
            # args and the remote shell mis-parses them.
            ok, _, unreachable = ssh_run(host, 'grep -q \"control_plane_url: https://holdfast.tail1c66ec.ts.net\" ~/.config/holdfastctl/config.yaml')
            if unreachable:
                print(f'  ERROR: could not reach {host} - {unreachable}')
                all_passed = False
            elif ok:
                print('  PASS: config points at Tailscale URL')
            else:
                print('  FAIL: config does not point at Tailscale URL')
                all_passed = False
    
    print()  # Blank line between devices

# Summary
if all_passed:
    print('ALL CHECKS PASSED')
    sys.exit(0)
else:
    print('SOME CHECKS FAILED')
    sys.exit(1)
" "$DEVICES_JSON"