from app.models import User, VoterSession


def test_user_and_session_link(db_session):
    u = User(hf_id="hf-123", username="alice")
    db_session.add(u)
    db_session.flush()
    vs = VoterSession(session_id="sess-1", user_id=u.id)
    db_session.add(vs)
    db_session.flush()
    got = db_session.get(VoterSession, "sess-1")
    assert got.user_id == u.id
    anon = VoterSession(session_id="sess-2")  # user_id defaults NULL = anonymous
    db_session.add(anon)
    db_session.flush()
    assert db_session.get(VoterSession, "sess-2").user_id is None
