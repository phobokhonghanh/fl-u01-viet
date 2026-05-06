# Test health check endpoint
from fastapi.testclient import TestClient
import sys
import os

# Add parent directory to path to import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app

client = TestClient(app)

def test_kaithhealthcheck():
    response = client.get("/kaithhealthcheck")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
