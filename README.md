# Buyer Operations contract implementation

This repository implements executable portions of the governing Buyer Operations specifications.
The Markdown contracts and machine-readable schemas in the repository root are authoritative; code
must fail closed where a deployment decision or executable schema is absent.

The implementation is not yet a production-complete Buyer Operations system. In particular, do not
activate provider effects or live cognition from this repository state. Capability activation remains
subject to `PRODUCTION-GATE-REGISTRY.yaml` and the open decisions in `DESIGN-TRUTH-LEDGER.md`.
Canonical reference mappings and the bindings that remain unavailable are enumerated in
`CANONICAL-REFERENCE-BINDING-AUDIT.md`; affected writes intentionally fail closed.

## Requirements

- Python 3.12
- `uv`
- PostgreSQL 17 for integration verification
- Temporal Python SDK 1.30.0; the SDK-managed 1.30.0 test server for workflow replay tests
- Docker only when using the disposable local PostgreSQL workflow

Install the exact locked runtime and development dependencies:

```bash
uv sync --locked --extra dev
```

The lock currently includes the PostgreSQL driver, generated-model tooling, JSON Schema validation,
and the cryptographic implementation used for encrypted artifacts and Ed25519 evidence checkpoints.

Control plane and Temporal worker require PostgreSQL 17. The control plane also requires
`BUYER_OPS_CONTROL_TOKEN`, `BUYER_OPS_PERMIT_SECRET` ≥32 bytes,
`BUYER_OPS_GATE_REGISTRY_PATH`, and `BUYER_OPS_RELEASE_PUBLIC_KEYS_JSON` (a JSON object
mapping release key IDs to base64url-encoded raw Ed25519 public keys). A Temporal worker embedding
requires `BUYER_OPS_DATABASE_DSN`, `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, a complete
`TEMPORAL_WORKER_CONFIGURATION_JSON` matching `WorkerConfiguration`, and a governed OT-01
`JourneyState` derivation. The published contracts do not currently provide that derivation, so the
standalone worker remains operationally unavailable rather than installing an implementation-defined
compiler interface.
The capture-side workflow starter likewise requires `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`,
`TEMPORAL_TASK_QUEUE`, and a complete `TEMPORAL_RUNTIME_POLICY_JSON` once any Temporal address is
configured. It does not supply a namespace or retry-policy default, and duplicate workflow starts
propagate Temporal's rejection.

```bash
uv run python -m buyer_ops_contracts.control_plane
```

## Verification

Run deterministic checks that do not need a database:

```bash
uv run python scripts/verify_contracts.py
uv run python scripts/verify_gate_registry.py
uv run python scripts/verify_completion_ledger.py
uv run python scripts/verify_migrations.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

PostgreSQL tests are skipped unless `BUYER_OPS_TEST_POSTGRES_DSN` identifies a disposable database.
The suite creates and removes contract tables and creates or reuses a constrained cluster-level test
role; never point it at retained or production data.

```bash
export BUYER_OPS_TEST_POSTGRES_DSN='postgresql://postgres:password@127.0.0.1:5432/buyer_ops_test'
uv run pytest tests/test_postgres_integration.py
```

The CI-pinned PostgreSQL 17 image can be used locally on an isolated port:

```bash
docker run --rm --name buyer-ops-contract-postgres17 \
  -e POSTGRES_PASSWORD=contract-test-only -e POSTGRES_DB=buyer_ops_test \
  -p 127.0.0.1:55433:5432 \
  postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73
export BUYER_OPS_TEST_POSTGRES_DSN='postgresql://postgres:contract-test-only@127.0.0.1:55433/buyer_ops_test'
uv run pytest tests/test_postgres_integration.py
```

CI installs from `uv.lock` with uv 0.11.16 and runs the same suite against a digest-pinned PostgreSQL
service plus the version-pinned Temporal test server.

## Migration order

Apply migrations in filename order:

1. `0001_canonical_records.sql`
2. `0002_evidence_ledger.sql`
3. `0003_identity_resolution.sql`
4. `0004_ontology_0_2.sql`
5. `0005_habitat_permits.sql`
6. `0006_ontology_0_3.sql`
7. `0007_inbound_events.sql`
8. `0008_operator_commands.sql`
9. `0009_control_plane.sql`
10. `0010_external_message_identity.sql`
11. `0011_operator_surface_1_1.sql`
12. `0012_ot01_acknowledgment.sql`
13. `0013_actor_tenant_authorization.sql`
14. `0014_release_activation.sql`
15. `0015_release_activation_concurrency.sql`
16. `0016_connector_credentials.sql`
17. `0017_platform_oauth_clients.sql`
18. `0018_platform_oauth_twilio.sql`
19. `0019_cognitive_credentials.sql`
20. `0020_oauth_return_origin.sql`
21. `0021_derived_contract_records.sql`

Each migration has a rollback script for an empty, unactivated installation. Rollback deliberately
refuses to discard populated canonical, derived-contract, evidence, identity, authority-decision,
or permit stores;
deployed data requires a reviewed forward-repair migration.

Runtime database roles must be non-superuser and must not have `BYPASSRLS`. Every repository operation
sets `app.tenant_id` transaction-locally, and migrations force row-level security on tenant data.

## Contract synchronization

After an authorized change to a governing JSON Schema, synchronize the packaged copy, manifest digest,
and generated Pydantic models:

```bash
uv run python scripts/sync_contracts.py --write
bash scripts/generate_models.sh
uv run python scripts/generate_ontology_fixtures.py
uv run python scripts/report_ontology_compatibility.py
uv run python scripts/verify_contracts.py
```

Do not hand-edit files under `src/buyer_ops_contracts/generated/`.
