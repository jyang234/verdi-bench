# Delete a document

`docsvc` is a small HTTP service for documents:

    GET    /docs/{id}   returns the document as JSON
    POST   /docs        creates a document
    PUT    /docs/{id}   retitles a document

Add a new endpoint:

    DELETE /docs/{id}

It removes the document with the given id. After a successful call the document
no longer exists. Wire the new route into the service alongside the existing
endpoints.
