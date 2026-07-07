# Bulk import

`ingestsvc` is a small bulk-import service backed by an in-memory row store. It
already exposes `POST /seed`, which loads a single row through a batch.

Add a `POST /import` endpoint, served alongside the existing route, that imports
a list of rows in one batch. The request body is JSON:

    {"rows": ["alice", "bob", "carol"]}

Each row is added to the batch and the batch is flushed to commit them. A row
can be rejected (an empty row is invalid); if any row is rejected the import
must fail — return a `422 Unprocessable Entity` — and commit nothing. On success
return `204 No Content`.

The store's `OpenBatch` opens a batch exposing `Add`, `Flush`, and `Discard`;
add an `Import` method to `core.Service` and wire the route to it.
