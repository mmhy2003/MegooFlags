# Evaluation spec

The shared flag evaluation spec and the conformance test suite (PRD S-7).

Every SDK in [`sdks/`](../sdks/README.md) must pass this suite, and it gates every SDK release, so all
SDKs evaluate flags, targeting rules and percentage rollouts the same way as the server.

| Path | What it is |
|---|---|
| [`evaluation.md`](evaluation.md) | The normative evaluation spec: context, order of evaluation, operators, segments, bucketing, prerequisites, error codes and the ruleset format |
| [`ruleset.schema.json`](ruleset.schema.json) | JSON Schema (2020-12) of the ruleset that server SDKs download |
| [`conformance/`](conformance/) | The language-neutral conformance suite: case files of ruleset, context and expected result, plus bucketing hash vectors |

The reference implementation is the Python engine in
[`api/src/megooflags_api/evaluation/`](../api/src/megooflags_api/evaluation/), which passes 100 % of the
suite.
