# de-dupe the retry logic in the notifier

While debugging a flaky test I noticed `notifier` has almost the same
exponential-backoff retry loop copy-pasted in three places (email, SMS, push),
and they've already drifted — the SMS one caps at 5 tries, the others at 3. Could
you pull it into one helper so they behave the same? Behavior should match the
email version (3 tries, base 200ms).
