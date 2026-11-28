# Import report: Flagsmith

Percentage rollouts are re-bucketed: MegooFlags hashes contexts differently from Flagsmith, so users can land in a different bucket than before.

| Resource | Key | Environment | Message |
|---|---|---|---|
| environment | development |  | key "Development" was renamed to "development" |
| flag | checkout-theme |  | key "Checkout Theme" was renamed to "checkout-theme" |
| flag | checkout-theme | prod | the override for segment has-plan disables the flag, which a rule cannot do: it serves default |
| flag | max_items | prod | its value differs from the first environment's: default-prod serves it here, and the flag serves default when it is off |
| identity | alice@example.com | prod | identity overrides are not imported: dark_mode |
| identity | bob | prod | identity overrides are not imported: dark_mode, checkout-theme |
| segment | beta-users |  | key "Beta Users" was renamed to "beta-users" |
| segment | deep |  | rules nested too deep: skipped; the segment matches no one |
| segment | everyone |  | has no conditions, so it matched everyone in Flagsmith; here it matches no one |
| segment | has-plan |  | key "Has Plan" was renamed to "has-plan" |
| segment | has-plan |  | the condition on plan uses IS_SET, which is not supported: the rules that use it are skipped |
| segment | has-plan |  | the condition on trial uses IS_NOT_SET, which is not supported: the rules that use it are skipped |
| segment | sampled |  | key "Sampled" was renamed to "sampled" |
| segment | sampled |  | the condition on user_id uses MODULO, which is not supported: the rules that use it are skipped |
| segment | sampled |  | a condition uses PERCENTAGE_SPLIT, which is not supported: the rules that use it are skipped |
| segment | sampled |  | no rule is left: the segment matches no one |
