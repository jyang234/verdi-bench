# Finalize an invoice

`billingsvc` is a small HTTP service for invoices:

    GET  /invoices/{id}   returns the invoice as JSON
    POST /invoices        creates an invoice

Add a new endpoint:

    POST /invoices/{id}/finalize

Finalizing an invoice marks it as finalized and issues a receipt for it. After a
successful call the invoice is flagged finalized and a receipt row exists for it.
Wire the new route into the service alongside the existing endpoints.
