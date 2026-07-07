from fastapi.testclient import TestClient
from app.main import app


def test_onboarding_markup_present():
    # The arena moved to "/arena" (Task 9 of the design refresh); "/" is now the landing page.
    html = TestClient(app).get("/arena").text
    assert 'id="onboard-banner"' in html
    assert 'id="onboard-dismiss"' in html
    # the banner is hidden by default (revealed by JS only for new visitors)
    assert 'class="onboard-banner"' in html and " hidden" in html
    # key hints on vote buttons
    assert "<kbd>" in html
