# app/envfile.py
"""Load the repo's .env into os.environ.

Why this exists: OPENROUTER_API_KEY was set in the repo's .env and was still invisible to every
script. Nothing loaded it — each consumer does a bare `os.environ["OPENROUTER_API_KEY"]` and no
dotenv dependency was ever installed — and because .env is gitignored, a git worktree does not
even receive a copy. The key was simultaneously "set" and unreadable, repeatedly.

Two rules keep it safe:
  1. The real environment ALWAYS wins. A value already in the environment is never overwritten,
     so `env -u FOO` and per-command overrides keep behaving exactly as before.
  2. A .env may NEVER choose the database. See DB_DESTINATION_VARS below.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path


class UnsafeEnvFile(RuntimeError):
    """A .env that would silently redirect the database. Always fatal — never a warning."""


# Variables that decide WHICH DATABASE the process opens. A .env is invisible at the call site,
# and the test suite drops and recreates every table — a file silently repointing this at the
# study DB is exactly how the study DB was destroyed on 2026-06-28. Choosing the database must
# stay an explicit act of the invoking shell, so a .env that declares one is a hard error.
DB_DESTINATION_VARS = frozenset({"BIO3D_DATABASE_URL", "BIO3D_DB_PATH", "BIO3D_DATA_DIR"})


def parse_env_file(text: str) -> dict[str, str]:
    """KEY=VALUE per line. Skips blanks and #-comments; tolerates `export ` and surrounding quotes."""
    pairs: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        pairs[key] = value
    return pairs


def find_env_file(root: Path) -> Path | None:
    """The .env governing `root`: its own, else the main checkout's when `root` is a worktree.

    A worktree's `.git` is a FILE reading `gitdir: <main>/.git/worktrees/<name>`, so the main
    checkout is the parent of that `.git` directory. Following it is what lets a worktree see the
    gitignored .env that only ever exists in the main checkout."""
    local = root / ".env"
    if local.is_file():
        return local

    pointer = root / ".git"
    if pointer.is_file():
        text = pointer.read_text(encoding="utf-8").strip()
        if text.startswith("gitdir:"):
            gitdir = Path(text[len("gitdir:") :].strip())
            for parent in gitdir.parents:
                if parent.name == ".git":
                    candidate = parent.parent / ".env"
                    return candidate if candidate.is_file() else None
    return None


def load_env_file(root: Path, environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Populate `environ` from the repo's .env; return the names actually set. A value already
    present is left alone (rule 1). Raises UnsafeEnvFile if the file names a database (rule 2)."""
    env = os.environ if environ is None else environ
    path = find_env_file(root)
    if path is None:
        return []

    pairs = parse_env_file(path.read_text(encoding="utf-8"))
    if unsafe := DB_DESTINATION_VARS & pairs.keys():
        raise UnsafeEnvFile(
            f"{path} sets {sorted(unsafe)} — a .env must never choose the database. The test "
            "suite drops and recreates every table, so a file that silently repoints it at the "
            "study DB destroys it. Set these in the shell, for the one command that needs them."
        )

    loaded = [k for k in pairs if k not in env]
    for key in loaded:
        env[key] = pairs[key]
    return loaded
