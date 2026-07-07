# Approval events

`pubsvc` is a small approvals service. It already exposes `POST /notify`, which
records an event in the audit log and publishes it on the event bus.

Add a `POST /approve` endpoint, served alongside the existing route, that
handles an approval. The request body is JSON:

    {"id": "loan-7"}

On approval the service must both record the approval in the audit log
(`audit.Write`) and publish an "approved" event on the bus (`bus.Publish`).
Add an `Approve` method to `core.Service`, wire the route to it, and return
`204 No Content`.
