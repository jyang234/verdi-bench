# rate limit the public API

We got hammered again last night — one client's retry loop ran up our bill and
degraded latency for everyone else. Can you put a basic rate limit on
`edgeproxy` before the weekend?

Fixed window is fine for now: 100 req/min per client IP, return 429 with a
`Retry-After` when they blow through it. An in-process counter is ok, we only run
the one instance today. We can move it to Redis later if we scale out.
