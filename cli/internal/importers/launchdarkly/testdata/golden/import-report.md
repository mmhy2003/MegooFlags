# Import report: LaunchDarkly

Percentage rollouts are re-bucketed: MegooFlags hashes contexts differently from LaunchDarkly, so users can land in a different bucket than before.

| Resource | Key | Environment | Message |
|---|---|---|---|
| flag | checkout-theme | production | the fallthrough is an experiment; experiments are not imported, the rollout is kept |
| flag | checkout-theme | production | prerequisite db-migration is a migration flag, which is not imported: skipped |
| flag | checkout-theme | staging | off variation is blue here, but a MegooFlags flag has one off variation: v2 is used |
| flag | db-migration |  | migration flags are not imported |
| flag | max-items | staging | has no off variation here; v0 is served when the flag is off |
| flag | newcheckout |  | key "NewCheckout" was renamed to "newcheckout" |
| flag | newcheckout | production | targets of context kind organization are not imported |
| flag | newcheckout | production | rule 3 (Enterprise orgs) skipped: it uses context kind organization |
| flag | newcheckout | production | rule 4 (Big spenders) skipped: it uses big segment big-spenders |
| segment | beta-users | production | included contexts of context kind organization are not imported |
| segment | beta-users | production | rule 2 (Half of the testers) matches a percentage of contexts, which segments do not support: skipped |
| segment | beta-users | staging | included contexts of context kind organization are not imported |
| segment | beta-users | staging | rule 2 (Half of the testers) matches a percentage of contexts, which segments do not support: skipped |
| segment | big-spenders | production | big segments (unbounded) are not imported; rules that use it are skipped |
| segment | big-spenders | staging | big segments (unbounded) are not imported; rules that use it are skipped |
| segment | internal-production |  | key "Internal-production" was renamed to "internal-production" |
| segment | internal-production | production | segment Internal differs between environments; imported as one segment per environment |
| segment | internal-staging |  | key "Internal-staging" was renamed to "internal-staging" |
| segment | internal-staging | staging | segment Internal differs between environments; imported as one segment per environment |
