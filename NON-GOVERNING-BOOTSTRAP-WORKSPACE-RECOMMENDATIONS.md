# Non-Governing Bootstrap and Workspace Recommendations

Status: recommendation only; specification-owner approval and publication are required. Nothing in
this document authorizes an implementation, canonical write, connector effect, or activation.

## Tenant bootstrap contract needed

A future governing contract should accept a complete owner-supplied, signed, and versioned record
bundle or define an equivalent admission protocol. It should identify the owner and source of every
identifier, lifecycle state, policy value, authorization scope, evidence reference, effective time,
expiry, and supersession rule. Missing inputs should produce `configuration_incomplete`. The runtime
should not synthesize service principals, operator policies, qualification criteria, actor grants,
or effect authorizations.

The contract should include atomicity, idempotency, concurrency, tenant ownership, rollback,
current-record validation, valid and invalid fixtures, and migration compatibility. Deployment
choices covered by OPEN-001 and OPEN-003 should remain externally supplied.

## Operator projection contract needed

A future `JourneyView` projection contract should define exact canonical sources, selection and
precedence rules, handling of ambiguity and supersession, state derivation, blocker and next-action
derivation, evidence labels, time sensitivity, version material, and ETag computation. It should
also define whether reads may refresh a separately governed projection; ordinary reads must never
mint canonical identities or authority.

## Mutation bindings needed

Appointment, assertion, suppression, and workflow actions should be added only through published
`OperatorCommand` types with exact target and payload schemas. Each path should bind caller and
tenant authority, current policy, expected versions, idempotency, evidence, Habitat checks where
applicable, and atomic canonical plus `CommandResult` persistence. No timezone, principal,
capability, action scope, or evidence reference should be selected by the implementation.
