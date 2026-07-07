# Account transfer

`walletsvc` is a small wallet service backed by an in-memory ledger. It already
supports deposits through `POST /deposit`.

Add a `POST /transfer` endpoint, served alongside the existing routes, that moves
money between two accounts. The request body is JSON:

    {"from": "alice", "to": "bob", "amount": 100}

A transfer debits `from` and credits `to` inside a single transaction. If `from`
has insufficient funds, the transfer must fail and move no money — return a
`422 Unprocessable Entity`.

The store's `BeginTx` opens a transaction exposing `Debit`, `Credit`, `Commit`,
and `Rollback`; add a `Transfer` method to `core.Service` and wire the route to
it.
