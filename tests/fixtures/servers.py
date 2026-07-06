"""Shared HTTP-server lifecycle for the serve/author/review suites [refactor 01 §2].

One context manager owns the thread-serve-shutdown choreography that was
hand-rolled per test file. ``serve_root``/``serve_experiment`` cover the
operator observer's two binding modes; suites with their own server factory
(author, review) construct the server themselves and hand it to
``running_server``.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

from harness.serve.server import make_server


@contextmanager
def running_server(srv):
    """Serve an already-constructed HTTP server on a daemon thread.

    Yields the base URL; shutdown/close/join are guaranteed however the test
    exits.
    """
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


@contextmanager
def serve_root(root):
    """The operator observer over a workspace root of experiments."""
    with running_server(make_server(None, root=root, port=0)) as base:
        yield base


@contextmanager
def serve_experiment(experiment_dir):
    """The operator observer bound to one experiment directory."""
    with running_server(make_server(experiment_dir, port=0)) as base:
        yield base
