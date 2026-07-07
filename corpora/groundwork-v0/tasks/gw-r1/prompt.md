# Ticket history endpoint

`deskflow` is a small support-desk HTTP service. Tickets can be fetched and
renamed through its API, and every rename records an entry in an audit log.

Add a new endpoint:

    GET /tickets/{id}/history

It returns the ticket together with the number of audit-log entries recorded for
it, as JSON in this shape:

    {"ticket": {"ID": "t1", "Subject": "…", "Status": "…"}, "audit_count": 3}

Return `404 Not Found` if the ticket does not exist.

The repository already exposes what you need to read a ticket and to count its
audit entries. Wire the new route into the service so it is served alongside the
existing endpoints.
