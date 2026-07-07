# Read receipts

`inboxsvc` serves messages over HTTP and records receipts through a pluggable
recorder (`delivery.Deliverer`, with in-memory and database-backed
implementations). It already records a **delivery** receipt when a caller
acknowledges a message through `POST /messages/{id}/receipt`.

Add **read receipts**: each `GET /messages/{id}` should record that the message
was read, once per fetch. Keep returning the message as JSON on success:

    {"ID": "m1", "Subject": "…", "Body": "…"}

Wire a recorder in for read receipts and thread it through the service so the
read path records one on every fetch. Keep serving `404` when the message does
not exist.
