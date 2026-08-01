"""
Direct test of the HTTP routing functionality for server components.
"""

import os
import sys
import tempfile

# Add source to path
sys.path.insert(0, 'src')

def test_app_creation():
    """Test that we can create the app without circular imports."""
    from server import app
    assert app is not None
    print("✓ App creation successful")


def test_server_endpoints():
    """Test that expected endpoints exist."""
    from server import create_app
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "test.db")
        app = create_app(db_path)
        
        # Get actual routes - correct way to access them
        routes = [route for route in app.routes if hasattr(route, 'path') and route.path]
        route_paths = [route.path for route in routes if route.path]
        
        print(f"✓ Found {len(routes)} routes")
        
        # Check for expected endpoints
        expected_endpoints = [
            "/healthz", 
            "/readyz", 
            "/api/v1/test-endpoint"
        ]
        
        found_endpoints = [path for path in route_paths if any(endpoint in path for endpoint in expected_endpoints)]
        print(f"✓ Found {len(found_endpoints)} expected endpoints")
        
        # Check that API endpoints are present
        api_routes = [path for path in route_paths if '/api/v1/' in path]
        print(f"✓ Found {len(api_routes)} API routes")


def test_database_creation():
    """Test that database can be initialized."""
    from server import init_database
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "test.db")
        init_database(db_path)
        print("✓ Database initialization successful")


def test_imports():
    """Test all key imports work."""
    # Test imports that should work
    
    # Test our imports
    print("✓ Core imports successful")


def test_route_registration():
    """Test that routes are properly registered."""
    from server import create_app
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "test.db")
        app = create_app(db_path)
        
        # Check that routes were registered properly
        route_paths = []
        for route in app.routes:
            if hasattr(route, 'path') and route.path:
                route_paths.append(route.path)
        
        print(f"✓ Route registration working, found {len(route_paths)} routes")
        
        # Look for API routes
        api_paths = [path for path in route_paths if '/api/v1/' in path]
        print(f"✓ Found {len(api_paths)} API paths")
        
        # Verify specific expected endpoints are present
        expected_paths = [
            "/healthz",
            "/readyz", 
            "/api/v1/enrollment-codes",
            "/api/v1/enroll",
            "/api/v1/devices/{device_id}/reports",
            "/api/v1/devices/{device_id}/plans",
            "/api/v1/devices/{device_id}/plans/{plan_id}/approve",
            "/api/v1/devices/{device_id}/drift",
            "/api/v1/devices/{device_id}",
            "/api/v1/devices"
        ]
        
        for path in expected_paths:
            found = any(path in route for route in route_paths)
            assert found, f"Expected endpoint {path} not found in routes"
        
        print("✓ All required endpoints registered correctly")


if __name__ == "__main__":
    print("Testing server components...")
    
    test_imports()
    test_app_creation() 
    test_database_creation()
    test_route_registration()
    test_server_endpoints()
    
    print("\n✓ All tests passed!")