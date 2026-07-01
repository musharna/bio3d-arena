from fastapi.testclient import TestClient
from app.main import app


def test_onboarding_markup_present():
    html = TestClient(app).get("/").text
    assert 'id="onboard-banner"' in html
    assert 'id="onboard-dismiss"' in html
    # the banner is hidden by default (revealed by JS only for new visitors)
    assert 'class="onboard-banner"' in html and " hidden" in html
    # key hints on vote buttons
    assert "<kbd>" in html
