# Governing Contract Authority Audit

**Audit date:** 2026-08-19  
**Superseded:** PR #1 / `8f528d9` (closed; must not merge)  
**Corrected candidate:** `spec/kernel-0.3.0-authority-corrected`  
**Review PR:** #2

## Result

The corrected candidate contains the complete 13-family Buyer Operations authority package. The concrete failure was stale manifest digests, which caused the verifier to reject the package. Those digests are corrected and the local verifier now passes.

| Property | Candidate result |
|---|---|
| Packaged families | 13 |
| Ontology | `buyer-ops/0.3.0` |
| Compatibility lineage | SCP-01 and explicit 0.2→0.3 migration |
| Schema/package bytes | All 13 pairs identical |
| Manifest digests | All 13 match packaged bytes |
| Generated models | All 13 synchronized |
| Contract verifier | Passed locally |
| Default-branch authority | Pending PR #2 review/merge |

## Disposition

PR #1 and `8f528d9` are historical and non-governing. PR #2 is the controlled publication path for the corrected authority package. Runtime activation remains separately gated by deployment evidence and the unresolved qualification-readiness, availability-booking, acknowledgment, and operator-mutation contracts.
