# Import report: Unleash

Percentage rollouts are re-bucketed: MegooFlags hashes contexts differently from Unleash, so users can land in a different bucket than before.

| Resource | Key | Environment | Message |
|---|---|---|---|
| flag | banner-config |  | key "Banner Config" was renamed to "banner-config" |
| flag | banner-config |  | has variants; disabled (value "") is served when the flag is off or no variant applies |
| flag | banner-config | development | depends on checkout-theme serving any of blue, green; a prerequisite has one variation: blue |
| flag | banner-config | development | depends on kill-db being disabled, which prerequisites cannot express: skipped |
| flag | banner-config | development | dependency gone does not exist: skipped |
| flag | checkout-theme |  | variant Dark Mode has a number payload, which is not imported: its name is the value |
| flag | checkout-theme |  | has variants; disabled (value "") is served when the flag is off or no variant applies |
| flag | checkout-theme | development | the overrides of variant blue are not imported |
| flag | checkout-theme | prod | strategy 3 (default) has random stickiness, which is not supported: it buckets by the context key |
| flag | new-checkout | prod | strategy 6 (default, Paused) is disabled: skipped |
| flag | new-checkout | prod | strategy 7 (remoteAddress) matches IP addresses, which are not supported: skipped |
| flag | new-checkout | prod | strategy 8 (applicationHostname) uses the applicationHostname strategy, which is not supported: skipped |
| flag | new-checkout | prod | strategy 9 (default, Recent apps) skipped: operator SEMVER_GTE is not supported |
| flag | new-checkout | prod | strategy 10 (default, Internal network) skipped: the constraint on remoteAddress matches IP addresses, which is not supported |
| flag | new-checkout | prod | strategy 11 (flexibleRollout, Random tenth) has random stickiness, which is not supported: it buckets by the context key |
| segment | beta-testers |  | the constraint on email is case-insensitive; it is imported case-sensitive |
| segment | everyone |  | has no constraints, so it matched everyone in Unleash; here it matches no one |
| segment | office-ips |  | key "Office IPs" was renamed to "office-ips" |
| segment | office-ips |  | constraints skipped: the constraint on remoteAddress matches IP addresses, which is not supported; the segment matches no one |
