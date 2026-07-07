# Dynamic publish endpoint

`eventsvc` is a small event-notification service. It registers subscribers
(`POST /subscribers/{id}`, which announces a `subscriber.created` event), reads
them back (`GET /subscribers/{id}`), and publishes events onto an internal bus.

Add a `POST /publish/{id}` endpoint that emits an event onto the bus whose name
comes from the request (an `event` query parameter), carrying the subscriber id
as the payload. Return `202 Accepted`. Wire the route in alongside the existing
endpoints. The notify layer already publishes onto the bus.
