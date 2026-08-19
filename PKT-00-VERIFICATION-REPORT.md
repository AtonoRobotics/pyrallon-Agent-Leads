# PKT-00 Verification Report

**Packet:** Schema generation and compatibility  
**Verified:** 2026-08-19  
**Disposition:** Complete for SCP-01 admission against ontology `buyer-ops/0.3.0`

This report preserves the 0.2 verification history below. Its current controlling evidence is
`SPECIFICATION-CLOSURE-PACKET.md`: 11 packaged contract families, 40 ontology roots, compatibility
and migration 0006, 167 passing tests including 21 PostgreSQL and 7 Temporal tests, static and format
checks, drift verification, secret scan, and package build.

## 1. Requirement trace

| PKT-00 requirement | Implementation | Verification |
|---|---|---|
| Draft 2020-12 structural validation | `registry.py`, `structural.py` | Both governing schemas pass `Draft202012Validator.check_schema`; valid and invalid records tested |
| Semantic validation | `semantic.py` | Ordering, expiry, grounding, action/proposal lifetime, 14-day limit, showing-only scope, qualification basis, compensation disclosure, and request/proposal correlation tests |
| Generated types | `generated/gateway.py`, `generated/ontology.py` | Generated Pydantic v2 types parse golden records |
| No generated drift | `scripts/generate_models.sh`, `scripts/verify_contracts.py` | Independent regeneration is byte-identical; timestamps disabled |
| Version negotiation | `SemanticPolicy`, gateway-pair validator | Unsupported proposal versions and request/proposal mismatches fail closed |
| Compatibility analysis | `ONTOLOGY-0.1-TO-0.2-COMPATIBILITY.json`, `compatibility.py` | 24 catalog records added; legacy abstract epistemic shape requires fail-closed forward repair |
| Pinned contract identity | `contracts.manifest.json`, `registry.py` | Schema IDs and SHA-256 digests checked at registry load and in CI |
| Golden fixtures | `tests/fixtures` | Valid and missing-required-field fixtures for all 38 ontology roots, gateway fixtures, and semantic negative mutations |

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
| Pytest | 133 non-PostgreSQL tests passed (154 total collected), including Temporal 1.30.0 replay/fault coverage, signed context/sufficiency tracing, fixed and unambiguous route eligibility/transitions, simulated provider-neutral adapters, provider-error normalization, pinned evaluated models, proposal admission, confirmed-milestone evidence admission, completion-ledger honesty, and nested/discriminated canonical-reference, actor-attribution, and canonical Evidence-source admission; all 21 integration tests passed through migration 0005 against the exact CI-pinned PostgreSQL 17 image, including the concurrent active-representation race |
| Ruff lint | Pass |
| Ruff format check | Pass |
| Mypy strict | Pass; 24 source files |
| Wheel build and reinstall | Pass |
| Installed CLI against valid request | `valid` |
| High-confidence secret-pattern scan | Clean |
| Wheel SHA-256 | `122eea077b1edfc3b8a53e464d28d0e823dfa96339cc30ef79ee6dd6c784a280` |

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

The 0.2.0 catalog revision has an explicit migration, rollback refusal, and forward-repair procedure.
`0004_ontology_0_2.sql` updates stored envelopes and declared defaults. It refuses to reinterpret
legacy `EpistemicItem` rows until they can be converted to a concrete epistemic type with the required
provenance. Unknown versions remain fail-closed and schema resources remain digest-pinned.

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

PKT-01 has resumed against ontology 0.3.0. OPEN-013 is resolved by canonical
`ConfirmedTransactionDate` with executable typed ownership, state, and source-digest admission.
PKT-02 compatibility is admitted against the 11-family manifest, and subsequent packets may proceed
in dependency order subject to their own gates and remaining deployment decisions.
