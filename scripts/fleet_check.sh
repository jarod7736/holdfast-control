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

devices = json.loads(sys.argv[1])
now = int(time.time())
all_passed = True

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
    
    # Determine host for SSH connection
    host = None
    if device_id == 'amd-halo':
        host = 'local'
    elif device_id == 'jarod7736-laptop':
        host = 'jarod7736-laptop'
    elif device_id == 'lobsterboy':
        host = 'lobsterboy-local'
    
    if host is None:
        print('  SKIP: No SSH mapping for this device')
    elif host == 'local':
        # Local machine - run commands directly
        try:
            # Check if holdfastctl-agent.timer is active
            result = None
            import subprocess
            try:
                result = subprocess.run(['systemctl', '--user', 'is-active', 'holdfastctl-agent.timer'], 
                                       capture_output=True, text=True, timeout=10)
            except:
                pass
            if result and result.returncode == 0 and 'active' in result.stdout:
                print('  PASS: holdfastctl-agent.timer is active')
            else:
                print('  FAIL: holdfastctl-agent.timer is not active')
                all_passed = False
        except Exception as e:
            print(f'  FAIL: Error checking local host: {e}')
            all_passed = False
            
        # Check config file content for amd-halo
        try:
            config_path = os.path.expanduser('~/.config/holdfastctl/config.yaml')
            with open(config_path, 'r') as f:
                content = f.read()
                if 'control_plane_url: https://holdfast.tail1c66ec.ts.net' in content:
                    print('  PASS: config points at Tailscale URL')
                else:
                    print('  FAIL: config does not point at Tailscale URL')
                    all_passed = False
        except Exception as e:
            print(f'  FAIL: Error checking local config: {e}')
            all_passed = False
    else:
        # SSH to remote host
        try:
            # Check if holdfastctl-agent.timer is active on remote host
            result = None
            import subprocess
            try:
                result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', host, 
                                       'systemctl --user is-active holdfastctl-agent.timer'], 
                                       capture_output=True, text=True, timeout=10)
            except:
                pass
            if result and result.returncode == 0 and 'active' in result.stdout:
                print('  PASS: holdfastctl-agent.timer is active')
            else:
                print('  FAIL: holdfastctl-agent.timer is not active')
                all_passed = False
        except Exception as e:
            print(f'  FAIL: SSH connection failed or error checking host: {e}')
            all_passed = False
            
        # Check config file content on remote host
        try:
            # Remote command must be a SINGLE ssh argument — ssh joins multiple
            # args and the remote shell mis-parses them.
            result = subprocess.run(
                ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', host,
                 'grep -q \"control_plane_url: https://holdfast.tail1c66ec.ts.net\" ~/.config/holdfastctl/config.yaml'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                print('  PASS: config points at Tailscale URL')
            else:
                print('  FAIL: config does not point at Tailscale URL')
                all_passed = False
        except Exception as e:
            print(f'  FAIL: SSH connection failed or error checking remote config: {e}')
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