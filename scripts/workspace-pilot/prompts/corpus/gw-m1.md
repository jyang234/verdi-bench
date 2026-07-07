# Per-account view counts

`accountsvc` serves accounts over HTTP and records signups through a pluggable
counter (`views.Counter`, with in-memory, no-op, and ledger-backed
implementations).

Add per-account **view counting**: each `GET /accounts/{id}` should record that
the account was viewed and return the running view count alongside the account.
Respond with JSON:

    {"account": {"ID": "a1", "Name": "…"}, "views": 3}

The count should increase by one on every `GET` of that account. Wire a counter
in for views and thread it through the service so the handler can report the
current total. Keep serving `404` when the account does not exist.
