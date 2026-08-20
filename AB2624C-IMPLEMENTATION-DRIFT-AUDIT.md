# Implementation Drift Audit at `ab2624c`

Status: non-governing implementation audit. This document does not amend any contract, schema,
manifest, ledger, packet, or production gate.

## Comparison basis

The dirty working tree was compared file by file with
`origin/review/kernel-0.3.0-authority@ab2624ce50b648fe376d81c637c94a5b5771c531`.
Published authority, rather than local code or tests, controlled every classification.

| Dirty file | Classification | Disposition |
|---|---|---|
| `src/buyer_ops_contracts/capture.py` | Unsupported invented semantics | Rejected automatic creation of journey effect authorizations during capture. Capture cannot choose a principal, grantor, action scope, evidence, or authorization lifetime. |
| `src/buyer_ops_contracts/control_plane.py` | Unsupported invented semantics and security regression | Rejected workspace projection activation, direct appointment/assertion/suppression mutations, the hidden `America/Chicago` default, write-on-GET authority creation, and removal of actor authentication. Retained the published fail-closed routes and security checks. |
| `src/buyer_ops_contracts/tenant_setup.py` | Owner/deployment input plus unsupported invented semantics | Rejected generated identifiers, active records, ten-year authorization, deployment mode, qualification policy, freshness, command scopes, service principal, and effect grants. The module was removed because no governing bootstrap contract admits this behavior. |
| `src/buyer_ops_contracts/workspace.py` | Unsupported invented semantics | Rejected the added service-principal field and removed the pre-existing provisional assembler and direct mutation helpers because deterministic projection and ETag rules are unpublished. |
| `tests/test_capture.py` | Unsupported test accommodation | Rejected relaxed mocks that concealed unexpected authority reads. |
| `tests/test_control_plane.py` | Unsupported test accommodation and security regression | Rejected mocked workspace success and deletion of fail-closed mutation and actor-authentication coverage. |
| `tests/test_tenant_setup.py` | Unsupported test authority | Removed tests that proved only structural validity of implementation-authored records and projections. Structural validity does not establish authority. |

## Semantically neutral correction

`SetupRejected` was moved to the general error module and its imports were updated. This preserves
the existing fail-closed connector and configuration error shape without retaining tenant-bootstrap
semantics.

## Result

Tenant bootstrap remains unavailable. `JourneyView` and workspace projections remain unavailable.
Workspace mutations remain unavailable and cannot bypass `OperatorCommand`, Habitat, current
authority, policy, evidence, version, and atomic result boundaries. Production activation remains
false. Unsupported local behavior was not admitted merely because tests could be made to pass.

