# Audit sent messages

`mailsvc` is a small HTTP service that sends messages:

    POST /send    stores (sends) a message, returning 202 Accepted

Add a send audit: whenever a message is sent, record an audit entry for it. The
repository already exposes an audit method for this. A successful `POST /send`
still stores the message and returns promptly with `202 Accepted`.
