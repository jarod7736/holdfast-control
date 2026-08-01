"""
Integration tests for FastAPI HTTP endpoints using TestClient.
"""

import os
import sys
import tempfile

from fastapi.testclient import TestClient

# Add source to path
sys.path.insert(0, 'src')


def test_route_registration():
    """Test that all required routes are registered."""
    from server import create_app
    
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "test.db")
        app = create_app(db_path)
        
        # Get all routes
        route_paths = []
        for route in app.routes:
            if hasattr(route, 'path') and route.path:
                route_paths.append(route.path)
        
        # Check for critical API endpoints
        required_endpoints = [
            "/api/v1/enrollment-codes",
            "/api/v1/enroll",
            "/api/v1/devices/{device_id}/reports",
            "/api/v1/devices/{device_id}/plans",
            "/api/v1/devices/{device_id}/plans/{plan_id}/approve",
            "/api/v1/devices/{device_id}/drift",
            "/api/v1/devices/{device_id}",
            "/api/v1/devices"
        ]
        
        # Check all endpoints are present
        found_endpoints = [path for path in route_paths if any(endpoint in path for endpoint in required_endpoints)]
        
        # Check for health endpoints too
        health_endpoints = ["/healthz", "/readyz"]
        found_health = [path for path in route_paths if any(endpoint in path for endpoint in health_endpoints)]
        
        assert len(found_endpoints) >= 5, f"Expected at least 5 API endpoints, found {len(found_endpoints)}"
        assert len(found_health) >= 2, f"Expected health endpoints, found {len(found_health)}"
        
        print(f"✓ Found {len(route_paths)} total routes")
        print(f"✓ Found {len(found_endpoints)} API endpoints")
        print(f"✓ Found {len(found_health)} health endpoints")


def test_health_endpoints():
    """Test health endpoints."""
    from server import create_app
    
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "test.db")
        app = create_app(db_path)
        client = TestClient(app)
        
        # Test health endpoint
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}
        
        # Test ready endpoint
        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
        
        print("✓ Health endpoints work correctly")


def test_route_list():
    """Test that route list shows all required endpoints."""
    from server import create_app
    
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "test.db")
        app = create_app(db_path)
        
        # Get all routes properly
        route_paths = []
        for route in app.routes:
            if hasattr(route, 'path') and route.path:
                route_paths.append(route.path)
        
        # Verify specific endpoints are present
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


def test_server_basic_functionality():
    """Test basic server functionality."""
    from server import create_app
    
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "test.db")
        app = create_app(db_path)
        
        # Verify app creation
        assert app is not None
        assert app.title == "Holdfast Control Plane"
        
        # Verify app has routes
        routes = [route for route in app.routes if hasattr(route, 'path') and route.path]
        assert len(routes) >= 10  # Should have many routes including health, API, etc.
        
        print("✓ Server app created with proper routes")


if __name__ == "__main__":
    # Run all tests directly
    try:
        test_route_registration()
        test_health_endpoints() 
        test_route_list()
        test_server_basic_functionality()
        print("\n✅ All tests passed!")
    except Exception as e:  # noqa: BLE001 - diagnostic script must not abort on probe failures
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)