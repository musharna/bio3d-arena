# tests/test_commission_preflight.py
"""A broken sandbox must abort the batch, not be recorded as models failing the task.

The incident: the batch ran with sandbox_prefix=["heavy-run"], and heavy-run (a systemd user
scope) fails inside a detached session. run_bpy shells out to [*prefix, blender, ...] and maps ANY
non-zero exit to status="error" against the MODEL — so three good bpy scripts (11930/11443/6970
chars, all valid) were recorded as three models failing Amanita muscaria, in 12ms each, with an
empty stderr. The /procedural board computes pass@1 from exactly those rows, so a harness failure
would have been published as a model's score.

Preflighting the sandbox removes the mechanism: if the wrapper cannot run Blender at all, nothing
is attempted and no attempt row is written — and the failure is loud, which an empty stderr is not.
"""

import pytest

from app.commission import HarnessError, preflight_sandbox


def test_preflight_passes_for_a_working_command():
    # `true` stands in for a wrapper that runs its argv: it exits 0 and runs the command.
    preflight_sandbox(sandbox_prefix=[], blender_bin="true")


def test_preflight_raises_when_the_sandbox_wrapper_fails():
    """`false` is a wrapper that exits non-zero without running Blender — exactly heavy-run's
    behaviour in a detached session."""
    with pytest.raises(HarnessError, match="sandbox"):
        preflight_sandbox(sandbox_prefix=["false"], blender_bin="true")


def test_preflight_raises_when_blender_is_missing():
    with pytest.raises(HarnessError, match="not-a-real-blender"):
        preflight_sandbox(sandbox_prefix=[], blender_bin="not-a-real-blender")


def test_preflight_error_names_the_full_command():
    """The message must show what was actually run — the empty stderr is what made the real
    incident take a database query to diagnose."""
    with pytest.raises(HarnessError) as exc:
        preflight_sandbox(sandbox_prefix=["false"], blender_bin="true")

    assert "false" in str(exc.value)
