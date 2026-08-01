#!/usr/bin/env python3
"""Debug script to understand command validation behavior"""

import tempfile
from pathlib import Path

from holdfastctl.validate import validate_manifest_file

# Test manifest with command field
manifest_with_command = """
device:
  id: test-device
  profile: linux-wsl
  command: /bin/bash -c "rm -rf /"
capabilities:
  opencode: {required: true, config_profile: personal-development}
  providers: [opencode-cloud]
  mcp_servers: [github]
credentials:
  - id: cred1
    reference: op://holdfast-lan/cred1/credential
    inject_as: LITELLM_API_KEY
"""

print("Testing manifest with command field:")
print(manifest_with_command)

with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
    f.write(manifest_with_command)
    f.flush()
    temp_path = f.name

try:
    errors = validate_manifest_file(Path(temp_path))
    print(f"Errors: {errors}")
    print(f"Number of errors: {len(errors)}")
finally:
    import os
    os.unlink(temp_path)

# Test manifest with invalid reference (should be allowed)
manifest_with_bad_reference = """
device:
  id: test-device
  profile: linux-wsl
capabilities:
  opencode: {required: true, config_profile: personal-development}
  providers: [opencode-cloud]
  mcp_servers: [github]
credentials:
  - id: cred1
    reference: /bin/bash -c "rm -rf /"
    inject_as: LITELLM_API_KEY
"""

print("\nTesting manifest with command-like string in reference field (should be allowed):")
print(manifest_with_bad_reference)

with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
    f.write(manifest_with_bad_reference)
    f.flush()
    temp_path = f.name

try:
    errors = validate_manifest_file(Path(temp_path))
    print(f"Errors: {errors}")
    print(f"Number of errors: {len(errors)}")
finally:
    import os
    os.unlink(temp_path)