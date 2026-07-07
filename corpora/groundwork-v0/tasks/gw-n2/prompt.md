# User summary endpoint

`userdirsvc` is a small user-directory service. Users can be fetched and renamed
through its HTTP API.

Add a `GET /users/{id}/summary` endpoint that returns a user together with a
derived field — the number of groups they belong to:

    {"User": {"ID": "u1", "Name": "Ann", "Email": "ann@example.com"}, "GroupCount": 3}

Return `200 OK` with the JSON body. Wire the route in alongside the existing
endpoints. The repository already exposes what you need to read a user and to
count their group memberships.
