# add a health check to the payments worker

Our orchestrator keeps killing the `payments-worker` pod because it has no
readiness probe, so in-flight settlements get interrupted. Can you add a
`GET /healthz` that returns 200 once the DB connection and the message-bus
consumer are both up, and 503 otherwise? Keep it cheap — it gets hit every couple
of seconds.
