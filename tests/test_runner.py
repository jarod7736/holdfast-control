"""
Simple test runner for Holdfast Control Phase 2 modules.
"""

import subprocess
import sys
from pathlib import Path


def run_module_tests():
    """Run tests for the new modules individually."""
    print("Running tests for new Phase 2 modules...")
    
    # List of test files to run
    test_files = [
        "tests/inspect_test.py",
        "tests/reconcile_test.py", 
        "tests/apply_test.py",
        "tests/backup_test.py",
        "tests/reporting_test.py"
    ]
    
    project_root = Path(__file__).parent
    all_passed = True
    
    for test_file in test_files:
        test_path = project_root / test_file
        if test_path.exists():
            print(f"\nRunning {test_file}...")
            try:
                result = subprocess.run([
                    sys.executable, "-m", "pytest", 
                    str(test_path),
                    "-v",
                    "--tb=short"
                ], cwd=project_root, capture_output=True, text=True, check=False)
                
                if result.returncode == 0:
                    print(f"✅ {test_file} passed")
                else:
                    print(f"❌ {test_file} failed")
                    print("STDOUT:", result.stdout)
                    print("STDERR:", result.stderr)
                    all_passed = False
                    
            except Exception as e:  # noqa: BLE001 - runner reports any failure and continues
                print(f"Error running {test_file}: {e}")
                all_passed = False
        else:
            print(f"⚠️  Test file not found: {test_file}")
    
    if all_passed:
        print("\n🎉 All new module tests passed!")
    else:
        print("\n💥 Some tests failed!")
    
    return all_passed

if __name__ == "__main__":
    success = run_module_tests()
    sys.exit(0 if success else 1)