# Contract package verification record

This record supersedes any broader verification claim attached to commit `1c01504`.

The 15-family authority package is admitted only when all of the following checks pass from a clean checkout of the commit under review:

1. `python scripts/sync_contracts.py --check` exits zero and leaves the Git worktree byte-for-byte unchanged.
2. `python scripts/verify_contracts.py` validates all Draft 2020-12 schemas, root/package byte identity, manifest identities and SHA-256 values, and regenerated-model byte identity.
3. `pytest -q tests/test_new_contract_acceptance.py` validates every record-type fixture and the governed cross-field behaviors for qualification, readiness, availability, booking, idempotency, DST, stale state, revocation, concurrency, and reconciliation.
4. `ruff check` and `ruff format --check` pass for the synchronization, semantic acceptance, and test files.
5. The two compatibility reports remain explicit that production writers are disabled until real persistence migrations are applied, rolled back, and reconstructed against PostgreSQL 17.

Passing this package does not activate connectors, calendar writes, qualification automation, or any other production capability. Those capabilities retain their own authority, security, migration, and production-evidence gates.
