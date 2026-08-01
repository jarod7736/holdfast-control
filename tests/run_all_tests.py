"""
Comprehensive test runner for Holdfast Control Phase 2 modules.
"""

import subprocess
import sys
from pathlib import Path


def run_tests():
    """Run all tests for the new modules."""
    print("Running comprehensive tests for Holdfast Control Phase 2 modules...")
    
    # Change to the project root directory
    project_root = Path(__file__).parent
    
    # Run pytest on all test files
    try:
        result = subprocess.run([
            sys.executable, "-m", "pytest", 
            str(project_root / "tests"),
            "-v",
            "--tb=short"
        ], cwd=project_root, capture_output=True, text=True, check=False)
        
        print("STDOUT:")
        print(result.stdout)
        if result.stderr:
            print("STDERR:")
            print(result.stderr)
        
        if result.returncode == 0:
            print("\n✅ All tests passed successfully!")
            return True
        else:
            print(f"\n❌ Some tests failed (exit code: {result.returncode})")
            return False
            
    except Exception as e:  # noqa: BLE001 - runner reports any failure and returns False
        print(f"Error running tests: {e}")
        return False

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)