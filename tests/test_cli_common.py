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
from harness.errors import VerdiRefusal
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


# --- OI-B: the uniform base + the enumeration completeness meta-test ----------
# [refactor 13 OI-B]. refusal_exit() maps VerdiRefusal uniformly, so a refusal a
# verb forgot to enumerate is a clean exit 2, not a traceback. The narrow form is
# unchanged, so code/message-differentiating ladders (grade, run, anchor) keep
# letting their un-named refusals propagate to a sibling handler.
def test_unmapped_verdi_refusal_surfaces_as_clean_exit_2(capsys):
    class _NovelRefusal(VerdiRefusal):
        pass

    with pytest.raises(typer.Exit) as excinfo:
        with refusal_exit():  # no enumeration — the uniform safety-net form
            raise _NovelRefusal("a brand-new refusal nobody enumerated")
    assert excinfo.value.exit_code == 2
    assert capsys.readouterr().err.strip() == "a brand-new refusal nobody enumerated"


def test_uniform_form_honors_explicit_code(capsys):
    class _NovelRefusal(VerdiRefusal):
        pass

    with pytest.raises(typer.Exit) as excinfo:
        with refusal_exit(code=1):  # uniform catch, exit-1 override
            raise _NovelRefusal("transient-style refusal")
    assert excinfo.value.exit_code == 1
    assert capsys.readouterr().err.strip() == "transient-style refusal"


def test_narrow_form_lets_unnamed_verdi_refusal_propagate():
    # Critical regression guard: the narrow enumeration must NOT swallow other
    # VerdiRefusals, or grade's code-1/code-2 nesting, run's NoTasksError ladder,
    # and anchor's CHAIN-BROKEN handler would all collapse to one code/message.
    class _Enumerated(VerdiRefusal):
        pass

    class _NotEnumerated(VerdiRefusal):
        pass

    with pytest.raises(_NotEnumerated):
        with refusal_exit(_Enumerated):  # catches EXACTLY _Enumerated
            raise _NotEnumerated("must reach a sibling handler, not exit here")


def test_every_refusal_exit_enumerated_type_is_a_verdi_refusal():
    """Every exception type named in a ``refusal_exit(...)`` enumeration across the
    CLIs is a VerdiRefusal, so the uniform base catches it. This is mechanical: it
    AST-scans the call sites and resolves each name against the harness exception
    registry — a new refusal a verb enumerates without reparenting turns this red
    [refactor 13 OI-B]."""
    import ast
    import builtins
    import importlib
    import pkgutil

    import harness

    # ValueError is a stdlib boundary type deliberately caught raw at a narrow
    # site (process record's bad --scores mapping); it is not reparentable and is
    # named here so the allowlist is explicit, not silent.
    stdlib_allowed = {"ValueError"}

    # 1. registry: every BaseException subclass defined under harness
    registry: dict[str, type] = {}
    for modinfo in pkgutil.walk_packages(harness.__path__, prefix="harness.",
                                         onerror=lambda _n: None):
        try:
            mod = importlib.import_module(modinfo.name)
        except Exception:  # noqa: BLE001 — an unimportable optional module is skipped
            continue
        for obj in vars(mod).values():
            if (isinstance(obj, type) and issubclass(obj, BaseException)
                    and obj.__module__.startswith("harness")):
                registry.setdefault(obj.__name__, obj)

    # 2. AST-scan every CLI shell for refusal_exit(...) positional arg names
    repo = __import__("pathlib").Path(harness.__file__).resolve().parent.parent
    cli_files = [repo / "harness" / "cli.py", repo / "harness" / "cli_common.py"]
    cli_files += sorted((repo / "harness").glob("*/cli.py"))
    enumerated: set[str] = set()
    for f in cli_files:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "refusal_exit"):
                for arg in node.args:  # positional only; code= is a keyword
                    if isinstance(arg, ast.Name):
                        enumerated.add(arg.id)

    assert enumerated, "no refusal_exit enumerations found — the AST scan is broken"

    # 3. each enumerated type is a VerdiRefusal (or a named stdlib boundary type)
    offenders = []
    for name in sorted(enumerated):
        if name in stdlib_allowed:
            assert issubclass(getattr(builtins, name), BaseException)
            continue
        cls = registry.get(name)
        if cls is None:
            offenders.append(f"{name} (not found in the harness exception registry)")
        elif not issubclass(cls, VerdiRefusal):
            offenders.append(f"{cls.__module__}.{name} is not a VerdiRefusal")
    assert not offenders, (
        "refusal_exit enumerates type(s) that are not VerdiRefusal (nor an "
        "allowlisted stdlib boundary type): " + "; ".join(offenders)
    )
