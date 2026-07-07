# Per-order view count

`ordersvc` is a small HTTP service for orders. An order can be fetched and
relabeled through its API:

    GET /orders/{id}     returns the order as JSON
    PUT /orders/{id}     relabels the order

Extend the read endpoint so it also reports how many times the order has been
viewed. `GET /orders/{id}` should return a per-order view count that increments
by one on every GET, as JSON in this shape:

    {"order": {"ID": "o1", "Label": "…", "Status": "…"}, "views": 3}

The first GET of an order reports `1`, the second `2`, and so on. Return
`404 Not Found` if the order does not exist. Wire the counting into the service
so it is served on the existing route.
