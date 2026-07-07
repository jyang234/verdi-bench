# CSV export for reports

Finance keeps asking if they can pull the monthly report into a spreadsheet
instead of screenshotting the dashboard. Could you add a
`GET /reports/{id}/export` to `reportgen` that streams the rows as CSV with a
header row?

Watch out for the description field — it sometimes has commas and the odd quote
in it, so we'll need proper RFC 4180 escaping or Excel mangles it. No rush, end
of sprint is fine.
