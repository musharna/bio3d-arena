from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_onboarding_card_markup_present():
    # The arena moved to "/arena" (Task 9 of the design refresh); "/" is now the landing page.
    html = client.get("/arena").text
    assert 'id="onboard-banner"' in html
    assert 'id="onboard-dismiss"' in html
    # first-run CARD (not the old one-line banner): why + 3 steps + keys + Start CTA
    assert 'class="onboard-card"' in html and " hidden" in html
    assert "onboard-steps" in html
    assert 'id="onboard-start"' in html
    assert "Inspect" in html and "Compare" in html and "Vote" in html
    assert "<kbd>" in html  # key hints


def test_onboarding_js_dismiss_is_persistent_and_wired():
    ajs = client.get("/static/arena.js").text
    # both the ✕ and the Start CTA dismiss; shown once (localStorage)
    assert "onboard-start" in ajs and "onboard-dismiss" in ajs
    assert "bio3d_onboarded" in ajs
