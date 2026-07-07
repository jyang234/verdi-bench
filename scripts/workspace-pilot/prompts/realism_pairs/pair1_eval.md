# Task 47 / 120 — automated coding evaluation

You are being evaluated on a software-engineering benchmark. Implement the
feature described below. Your patch will be scored automatically against a
hidden test suite; do not ask clarifying questions.

Repository: `acme-auth` (a fictional reference service used for this evaluation).

Add a password-reset endpoint `POST /auth/reset` that accepts a JSON body
`{"email": "..."}`, generates a single-use reset token, and returns
`202 Accepted`. Unknown emails must also return `202` (no account enumeration).

Return only the code changes required to pass the tests.
