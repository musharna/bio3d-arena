# tests/test_envfile.py
"""The repo's .env must actually reach os.environ.

The bug this closes: OPENROUTER_API_KEY was set in /home/mjarnold/bio3d-arena/.env, but every
consumer does a bare os.environ["OPENROUTER_API_KEY"], no dotenv dependency was installed, and
.env is gitignored — so a git worktree never even receives a copy. The key was "set" and
simultaneously invisible to Python, repeatedly.

The safety rail: a .env must never choose WHICH DATABASE the process opens. The test suite drops
and recreates every table; a file that silently repoints BIO3D_DATABASE_URL at the study DB is
exactly how the study DB was destroyed on 2026-06-28. Declaring one is a hard error, not a warning.
"""

import pytest

from app.envfile import (
    DB_DESTINATION_VARS,
    UnsafeEnvFile,
    find_env_file,
    load_env_file,
    parse_env_file,
)


def test_parses_pairs_ignoring_comments_blanks_quotes_and_export():
    text = "\n".join(
        [
            "# a comment",
            "",
            "OPENROUTER_API_KEY=sk-or-v1-abc",
            'QUOTED="quoted value"',
            "SINGLE='single'",
            "export EXPORTED=yes",
        ]
    )
    assert parse_env_file(text) == {
        "OPENROUTER_API_KEY": "sk-or-v1-abc",
        "QUOTED": "quoted value",
        "SINGLE": "single",
        "EXPORTED": "yes",
    }


def test_loads_into_environ(tmp_path):
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-or-v1-abc\n")
    environ: dict[str, str] = {}

    loaded = load_env_file(tmp_path, environ)

    assert environ["OPENROUTER_API_KEY"] == "sk-or-v1-abc"
    assert loaded == ["OPENROUTER_API_KEY"]


def test_real_environment_always_wins(tmp_path):
    """`env -u FOO` and explicit overrides must keep working — a file never overrides the shell."""
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=from-file\n")
    environ = {"OPENROUTER_API_KEY": "from-shell"}

    loaded = load_env_file(tmp_path, environ)

    assert environ["OPENROUTER_API_KEY"] == "from-shell"
    assert loaded == []


@pytest.mark.parametrize("var", sorted(DB_DESTINATION_VARS))
def test_env_file_choosing_the_database_fails_loud(tmp_path, var):
    (tmp_path / ".env").write_text(f"{var}=sqlite:///data/study/arena-study.db\n")

    with pytest.raises(UnsafeEnvFile, match=var):
        load_env_file(tmp_path, {})


def test_missing_env_file_is_not_an_error(tmp_path):
    assert load_env_file(tmp_path, {}) == []


def test_worktree_follows_git_pointer_to_the_main_checkout(tmp_path):
    """A worktree has no .env of its own (.env is gitignored), so resolve the main checkout's."""
    main = tmp_path / "repo"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (main / ".env").write_text("OPENROUTER_API_KEY=sk-or-v1-abc\n")

    worktree = tmp_path / "repo" / ".claude" / "worktrees" / "wt"
    worktree.mkdir(parents=True)
    # in a worktree, .git is a FILE pointing at <main>/.git/worktrees/<name>
    (worktree / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n")

    assert find_env_file(worktree) == main / ".env"

    environ: dict[str, str] = {}
    load_env_file(worktree, environ)
    assert environ["OPENROUTER_API_KEY"] == "sk-or-v1-abc"


def test_local_env_file_wins_over_the_main_checkout(tmp_path):
    main = tmp_path / "repo"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (main / ".env").write_text("OPENROUTER_API_KEY=from-main\n")

    worktree = main / ".claude" / "worktrees" / "wt"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n")
    (worktree / ".env").write_text("OPENROUTER_API_KEY=from-worktree\n")

    environ: dict[str, str] = {}
    load_env_file(worktree, environ)
    assert environ["OPENROUTER_API_KEY"] == "from-worktree"
