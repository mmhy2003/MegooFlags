# Security policy

## Supported versions

Security fixes go into the latest release. Before 1.0.0, only the latest `0.Y.Z` release is supported:
upgrade to it to get a fix. The SDKs, the CLI and the Terraform provider follow the same rule, each for
its own latest release.

## Reporting a vulnerability

Report vulnerabilities privately through GitHub private vulnerability reporting:
https://github.com/mmhy2003/MegooFlags/security/advisories/new

Do not open a public issue, discussion or pull request for a vulnerability.

Please include:

- the affected component (API, web UI, worker, an SDK, the CLI or the Terraform provider) and version,
- the deployment details that matter (single node or multi-node, TLS mode, proxy in front),
- the steps to reproduce, or a proof of concept,
- the impact you expect, and any fix you suggest.

## What happens next

- You get a first response within 7 days.
- We confirm the problem, agree on its severity with you and keep you updated while we work on a fix.
- We follow coordinated disclosure: the advisory is published once a fixed release is out, and we credit
  you in it unless you prefer not to be named. Please keep the details private until then.

## Hardening your deployment

How to run MegooFlags securely (TLS, trusted proxies, secrets, SDK keys and roles) is described at
https://mmhy2003.github.io/MegooFlags/deploy/security/.
