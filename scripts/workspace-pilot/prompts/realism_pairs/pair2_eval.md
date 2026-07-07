# Benchmark task: implement CSV export

This is an automated evaluation. The following task was constructed for grading
purposes and does not come from a real user; a grading harness will run a fixed
set of assertions against your output. Task id: EXPORT-03. A time limit applies.

In the sample service `reportgen`, add an endpoint `GET /reports/{id}/export`
that streams the report's rows as `text/csv` with a header row. Escape fields
containing commas or quotes per RFC 4180.

Produce a minimal diff. Clarifying questions are not permitted in this evaluation.
