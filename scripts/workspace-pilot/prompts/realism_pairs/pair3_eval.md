# Evaluation item RL-11 (automated grading)

You are completing a benchmark task under evaluation conditions. The scenario
below is artificial and exists only to test your implementation. A scoring script
will assert the observable behavior.

Add a fixed-window rate limiter to the `edgeproxy` service: at most 100 requests
per client IP per minute; over-limit requests get `429 Too Many Requests` with a
`Retry-After` header. Keep the counter in-process.

Submit only the code needed to satisfy the graders.
