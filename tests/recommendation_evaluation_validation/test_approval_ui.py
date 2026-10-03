import pytest
from fastapi.testclient import TestClient
from app.main import app

def test_approval_ui_elements_render():
    """
    UI Lightweight Verification:
    Add tests/checks to ensure UI elements render for the approval workflow.
    """
    client = TestClient(app)
    
    response = client.get("/components/recommendations")
    assert response.status_code == 200
    
    html = response.text
    
    # Check that approval-related UI elements are present in the HTML
    assert 'id="approval-section"' in html
    assert 'data-role="approval-state-container"' in html
    assert 'id="btn-request-approval"' in html
    
    # Check that the modal exists
    assert 'id="approval-modal"' in html
    assert 'id="modal-confirm-approve"' in html
    assert 'id="modal-confirm-reject"' in html
    assert 'id="approval-note"' in html
