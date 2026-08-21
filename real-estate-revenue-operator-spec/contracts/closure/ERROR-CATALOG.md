# Closure error catalog

| Code | HTTP | Meaning | Retry |
|---|---:|---|---|
| `TENANT_SCOPE_DENIED` | 403 | actor or reference crosses tenant boundary | no |
| `CANONICAL_VERSION_CONFLICT` | 409 | expected version is stale | reread |
| `APPROVAL_VERSION_CONFLICT` | 409 | approval was already decided | no |
| `EFFECT_CONTEXT_STALE` | 409 | preview, policy, connector or constraint digest changed | re-preview |
| `IDEMPOTENCY_REPLAY` | 200 | same command already has a durable outcome | no |
| `WORKFLOW_SIGNAL_PENDING` | 202 | command persisted; Temporal signal not yet observed | poll |
| `REPRESENTATION_CONFLICT` | 409 | competing active representation exists | resolve |
| `REFERENCE_INVALID` | 422 | discriminator, cardinality or temporal rule failed | correct |
| `EVIDENCE_UNBOUND` | 422 | event, SLO or completion lacks governed binding | correct |
| `ACTIVATION_INVALID` | 403 | release hash/evidence/accessibility binding is stale or incomplete | no |

