# Read-activity events

`feedsvc` serves activity-feed items over HTTP and emits activity events through
a pluggable emitter (`emit.Emitter`, with log-backed, no-op, and bus-backed
implementations). It already emits an event when a caller reacts to an item
through `POST /feed/{id}/react`.

Add **read-activity events**: each `GET /feed/{id}` should emit an activity event
recording that the item was read, once per fetch. Keep returning the item as
JSON on success:

    {"ID": "f1", "Actor": "…", "Verb": "…"}

Wire an emitter in for read activity and thread it through the service so the
read path emits one event on every fetch. Keep serving `404` when the item does
not exist.
