"""Stage-verb registration must not swallow transitive import errors [refactor 01 §4 D1].

``harness/cli.py`` attaches each stage's subcommands inside a
``try/except ModuleNotFoundError`` meant to tolerate a genuinely-absent stage
module. Catching the bare exception type also swallowed a *transitive*
ModuleNotFoundError raised inside a present stage CLI (a broken dependency
import), silently dropping the verb instead of failing loudly. These tests run
a fresh interpreter with an import hook simulating each case, because the
registration loop runs once at ``harness.cli`` import time.
"""

from __future__ import annotations

import subprocess
import sys

# A meta-path hook template: intercepts the import of one stage CLI module and
# makes it behave as {body} — either a present module whose body raises a
# transitive ModuleNotFoundError, or a genuinely-absent module.
_HOOK = """\
import importlib.abc, importlib.machinery, sys

TARGET = "harness.serve.cli"


class Hook(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != TARGET:
            return None
        {find_spec_body}

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        {exec_body}


sys.meta_path.insert(0, Hook())
import harness.cli  # noqa: F401  (runs the stage-registration loop)
{after_import}
"""


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )


def test_transitive_module_not_found_aborts_registration_loudly():
    """A stage CLI whose import dies on a MISSING DEPENDENCY must abort the
    ``harness.cli`` import with that error — not vanish from the verb list."""
    code = _HOOK.format(
        # the stage module is present (a spec resolves) …
        find_spec_body="return importlib.machinery.ModuleSpec(fullname, self)",
        # … but its body hits a missing transitive dependency
        exec_body="import somefakelib_that_does_not_exist  # noqa: F401",
        after_import="",
    )
    result = _run(code)
    assert result.returncode != 0, (
        "importing harness.cli swallowed a transitive ModuleNotFoundError raised "
        "inside a present stage CLI — the verb silently vanished instead of the "
        f"import aborting loudly; stderr:\n{result.stderr}"
    )
    assert "somefakelib_that_does_not_exist" in result.stderr


def test_genuinely_absent_stage_module_is_still_tolerated():
    """The tolerance the except clause exists for: a stage module that does not
    exist at all skips its verbs and registers everything else."""
    code = _HOOK.format(
        # the stage module itself is absent: the finder raises the same
        # ModuleNotFoundError (name=the module) a real absence produces
        find_spec_body=(
            "raise ModuleNotFoundError("
            "f\"No module named {{fullname!r}}\", name=fullname)"
        ),
        exec_body="raise AssertionError('never loaded')",
        after_import=(
            "import typer\n"
            "cmd = typer.main.get_command(harness.cli.app)\n"
            "names = set(cmd.commands)\n"
            "assert 'serve' not in names, names  # the absent stage's verb is skipped\n"
            "assert 'grade' in names, names      # other stages still registered\n"
        ),
    )
    result = _run(code)
    assert result.returncode == 0, (
        f"a genuinely-absent stage module must be tolerated;\nstderr:\n{result.stderr}"
    )
