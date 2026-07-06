"""The shared CLI kit maps refusals + resolves actors uniformly [refactor 02 §3].

The stage CLIs delegate their two repeated idioms — refusal→exit-code mapping
and actor→EventContext resolution — to :mod:`harness.cli_common`. These exercise
the kit's observable contract (exit code, stderr text, EventContext fields) that
the 27 CliRunner suites rely on staying byte-identical after the extraction.
"""

from __future__ import annotations

import getpass

import pytest
import typer

from harness.cli_common import event_context, refusal_exit, resolve_actor_or_exit
from harness.ledger.actor import ActorResolutionError


def test_refusal_exit_maps_enumerated_error_to_exit_2(capsys):
    with pytest.raises(typer.Exit) as excinfo:
        with refusal_exit(ValueError):
            raise ValueError("boom refusal")
    assert excinfo.value.exit_code == 2
    assert capsys.readouterr().err.strip() == "boom refusal"


def test_refusal_exit_honors_explicit_code(capsys):
    with pytest.raises(typer.Exit) as excinfo:
        with refusal_exit(RuntimeError, code=1):
            raise RuntimeError("transient")
    assert excinfo.value.exit_code == 1
    assert capsys.readouterr().err.strip() == "transient"


def test_refusal_exit_does_not_catch_unenumerated_error():
    # A refusal type not in the enumeration propagates (surfaces loudly) rather
    # than being swallowed into a default exit — the fail-loud directive.
    with pytest.raises(KeyError):
        with refusal_exit(ValueError):
            raise KeyError("unmapped")


def test_refusal_exit_passes_through_on_success():
    with refusal_exit(ValueError):
        result = 21 * 2
    assert result == 42


def test_resolve_actor_or_exit_returns_explicit_flag():
    assert resolve_actor_or_exit("alice") == "alice"


def test_resolve_actor_or_exit_refuses_when_unresolvable(capsys, monkeypatch):
    def _no_user():
        raise OSError("no login name")

    monkeypatch.setattr(getpass, "getuser", _no_user)
    with pytest.raises(typer.Exit) as excinfo:
        resolve_actor_or_exit(None)
    assert excinfo.value.exit_code == 2
    assert "--actor" in capsys.readouterr().err


def test_event_context_stamps_dir_name_and_resolved_actor(tmp_path):
    exp_dir = tmp_path / "my-experiment"
    exp_dir.mkdir()
    ctx = event_context(exp_dir, "bob")
    assert ctx.experiment_id == "my-experiment"
    assert ctx.actor == "bob"


def test_event_context_refuses_unresolvable_actor(capsys, monkeypatch):
    def _no_user():
        raise KeyError("no passwd entry")

    monkeypatch.setattr(getpass, "getuser", _no_user)
    with pytest.raises(typer.Exit) as excinfo:
        event_context("/tmp/whatever", None)
    assert excinfo.value.exit_code == 2


def test_resolve_actor_error_type_is_stable():
    # The kit keys off the ledger's actor-resolution error; guard the import so a
    # rename surfaces here rather than as a silently un-caught refusal.
    assert issubclass(ActorResolutionError, RuntimeError)
