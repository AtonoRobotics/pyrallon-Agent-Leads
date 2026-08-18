# PKT-00 Verification Report

**Packet:** Schema generation and compatibility  
**Verified:** 2026-08-18  
**Disposition:** Implemented; locally verified; repository CI reproduction required for final packet closure

## 1. Requirement trace

| PKT-00 requirement | Implementation | Verification |
|---|---|---|
| Draft 2020-12 structural validation | `registry.py`, `structural.py` | Both governing schemas pass `Draft202012Validator.check_schema`; valid and invalid records tested |
| Semantic validation | `semantic.py` | Ordering, expiry, grounding, action/proposal lifetime, 14-day limit, showing-only scope, qualification basis, compensation disclosure, and request/proposal correlation tests |
| Generated types | `generated/gateway.py`, `generated/ontology.py` | Generated Pydantic v2 types parse golden records |
| No generated drift | `scripts/generate_models.sh`, `scripts/verify_contracts.py` | Independent regeneration is byte-identical; timestamps disabled |
| Version negotiation | `SemanticPolicy`, gateway-pair validator | Unsupported proposal versions and request/proposal mismatches fail closed |
| Compatibility analysis | `compatibility.py` | Required-field addition is breaking; optional-field addition is reader-compatible |
| Pinned contract identity | `contracts.manifest.json`, `registry.py` | Schema IDs and SHA-256 digests checked at registry load and in CI |
| Golden fixtures | `tests/fixtures` | Gateway request/proposal and Texas written agreement, plus negative mutation fixtures |

## 2. Changed-scope inventory

- Python package `buyer-ops-contracts` with a fail-closed CLI and importable validation API.
- Packaged, hash-pinned copies of both governing JSON Schemas.
- Deterministically generated Pydantic v2 server-side models.
- Structural, semantic, gateway-pair, compatibility, and gate-registry tests.
- CI workflow covering regeneration drift, lint, formatting, strict typing, and tests.
- No runtime provider, connector, canonical database, Temporal, or external effect was activated.

## 3. Verification evidence

| Check | Result |
|---|---|
| Contract source/package/hash/model synchronization | Pass |
| Pytest | 15 passed |
| Ruff lint | Pass |
| Ruff format check | Pass |
| Mypy strict | Pass; 8 source files |
| Wheel build and reinstall | Pass |
| Installed CLI against valid request | `valid` |
| High-confidence secret-pattern scan | Clean |
| Wheel SHA-256 | `e9584bf172c91ed8c345de54e3a1257f75f4814b3254a8df5f30dea1b6854904` |

Reproduction commands:

```bash
python -m pip install -e '.[dev]'
python scripts/verify_contracts.py
ruff check .
ruff format --check .
mypy
pytest
python -m pip wheel . --no-deps --wheel-dir dist
```

## 4. Compatibility and migration disposition

PKT-00 creates validation libraries and does not mutate canonical data, so no database rollback is
applicable. Schema evolution is fail-closed: unknown versions are rejected, schema resources are
digest-pinned, and changes classified as breaking require an explicit version and migration plan.
The compatibility analyzer is intentionally conservative; it is not permission to auto-promote a
schema change.

## 5. Operational and security disposition

This packet has no live service, secret, provider credential, network route, or external effect.
Operational observability begins when a service embeds the validator; callers must publish stable
violation codes as bounded metrics and must not place record payloads in metric labels. The package
does not log validated payloads.

## 6. Independent completion reconstruction

An independent reviewer can reconstruct the result by installing the locked development dependencies,
running `scripts/verify_contracts.py`, then running lint, strict typing, tests, and the wheel build.
CI encodes the same checks. Packet closure should be recorded only after that workflow passes from a
clean repository checkout.

## 7. Next dependency

PKT-01 (PostgreSQL canonical CRM and ontology) and PKT-02 (evidence ledger and artifact boundary) may
begin in parallel once the repository CI reproduces this report. Their storage migrations must consume
these contracts; they must not reimplement or weaken semantic admission inside ORM models.


