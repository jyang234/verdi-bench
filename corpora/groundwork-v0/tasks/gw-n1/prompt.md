# Create-product endpoint

`catalogsvc` is a small product-catalog service. Products can be fetched and
renamed through its HTTP API.

Add a `POST /products` endpoint that creates a new product from a JSON body:

    {"ID": "p9", "Name": "Gadget", "Price": 250}

On success return `201 Created`. Wire the route into the service alongside the
existing endpoints. The repository already exposes what you need to persist a
new product (and to record an audit entry, if you want one).
