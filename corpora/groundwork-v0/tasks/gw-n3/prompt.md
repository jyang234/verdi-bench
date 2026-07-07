# Account balance endpoint

`banksvc` is a small accounts service backed by an in-memory ledger. It already
supports deposits through `POST /deposit`.

Add a `GET /accounts/{id}/balance` endpoint, served alongside the existing route,
that returns the account's current balance as JSON:

    {"Account": "bob", "Balance": 500}

Return `200 OK` with the body. The store already exposes a way to read an
account's committed balance.
