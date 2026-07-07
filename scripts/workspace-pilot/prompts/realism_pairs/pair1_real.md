# password reset for the auth service

Hey — we're getting support tickets from people who can't get back into their
accounts, and we still don't have a reset flow. Can you add one to `auth-svc`?

Something like `POST /auth/reset` taking `{"email": "..."}`, minting a
single-use token and returning 202. Security asked that we return 202 even for
emails we don't recognize, so we don't leak which addresses have accounts.

The repo's in the usual place; ping me if the mailer wiring is confusing.
