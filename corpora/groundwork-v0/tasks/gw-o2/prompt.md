# Rate-limited processing

`limitsvc` is a small service that runs work under a concurrency limiter. It
already supports one unit of work through `POST /run`, which holds a limiter
slot while it runs.

Add a `POST /process` endpoint, served alongside the existing route, that
processes a request payload under the same limiter. The request body is JSON:

    {"payload": "some-work"}

Processing first validates the payload: an empty payload is invalid, and the
request must fail without processing anything — return a `422 Unprocessable
Entity`. A valid payload is processed and the request returns `204 No Content`.

The limiter's `Acquire` takes a slot and `Release` returns one; add a `Process`
method to `core.Service` and wire the route to it.
