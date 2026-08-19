# Governing Contract Authority Audit

**Audit date:** 2026-08-19  
**Superseded comparison:** `origin/spec/open-025-027` at `8f528d9` (open PR #1; must not merge)

## Result

The local executable registry must not currently be represented as a wholly published governing
registry. The candidate branch publishes four named families. The local registry advertises thirteen,
including locally authored revisions and families absent from the candidate branch.

| Family | Local | Candidate branch | Authority classification |
|---|---:|---:|---|
| `authority_activation_fair_housing` | 1.0.0 | 1.0.0 | Exact candidate schema digest verified; PR not admitted |
| `closure` | 1.1.0 | 1.0.0 | Local revision is not published |
| `gateway` | 1.1.0 | 1.1.0 | Version matches; payload differs and requires reconciliation |
| `ontology` | 0.3.0 | 0.1.0 | Local revisions 0.2.0/0.3.0 are not published on the governing branch |
| `gateway_runtime` | 1.1.0 | absent | Local-only/provisional |
| `habitat` | 1.0.0 | absent | Local-only/provisional |
| `temporal` | 1.1.0 | absent | Local-only/provisional |
| `context` | 1.1.0 | absent | Local-only/provisional |
| `operator_surface` | 1.1.0 | absent | Local-only/provisional |
| `telemetry_slo` | 1.0.0 | absent | Local-only/provisional |
| `ot01_ingress` | 1.1.0 | absent | Local-only/provisional |
| `connector_gateway` | 1.0.0 | absent | Local-only/provisional |
| `release_activation` | 1.1.0 | absent | Local-only/provisional |

The branch manifest also records hashes for its inherited ontology, gateway, and closure resources
that do not match the bytes stored at the manifest resource paths in that same commit:

| Family | Manifest digest | Raw resource digest at `8f528d9` |
|---|---|---|
| `closure` | `00760f5156a9ec67fb79611d8c01d3d8e22a63dd16a2667529f81ec2903a9564` | `6c485ddf4a66fcb0e30671277f28e507186f5b20356ecc1d7e9808efb74a0894` |
| `gateway` | `52d80302f683fc7d15206e25819416542a3d4e56f8698392755eda431362b136` | `ecdb0f21e794f04316ee6f5ae7f223144b51471d4aaf8dfdbf042d58a3e69c96` |
| `ontology` | `cd6c8b12393e586919322e9b8e876eb05be2b7f1a3590dd6b014f2f44bf089bb` | `112314a8be63773d3dd56d3658366d21945e9d5e4fa97a9ec81dfb09eaf5f77f` |

An archive of commit `8f528d9` run with its own `scripts/verify_contracts.py` exits with
`manifest digest drift: closure`. The mismatch is caused by raw trailing-byte differences, but the
published verifier compares raw bytes and therefore fails exactly as committed. This must be
resolved by a specification-owner publication; implementation must not select replacement bytes or
rewrite the governing hashes.

GitHub Actions run `32278232822` did not execute the verifier: GitHub reports that the job was not
started because of an account billing/spending-limit condition. It therefore supplies no passing CI
evidence for the branch and does not contradict the locally reproduced verifier failure.

PR #1 remains open, has no approving review decision, reports merge state `UNSTABLE`, and has one
failed required check. Its contract text labels itself governing, but repository admission is not
complete while the proposed revision is unmerged and its committed gate cannot pass. Runtime work
against OPEN-025–027 is therefore provisional compatibility work pending owner admission.

The specification owner has determined that PR #1 is not a valid resolution for the current system
and must not be merged. Commit `8f528d9` is based on the obsolete slim package: four families,
ontology `buyer-ops/0.1.0`, and none of the nine additional family schemas consumed by the local
0.3.0 kernel. The proposed OPEN-025–027 publication is superseded, not governing.

## Admission consequence

- Preserve local implementation, migrations, and tests as provisional compatibility work.
- Do not describe local-only families or unpublished revisions as governing or packet-complete.
- Do not revert or delete preserved work until the specification owner publishes an explicit
  compatibility/migration decision.
- Continue provisional compatibility testing only against the exact OPEN-025–027 candidate schema,
  whose local bytes match the proposed digest
  `99c0c2d5ee6a226ee038492ca28418d5b1b9559c06fe9e880b483e182d6b6b3a`.
- Rebuild the executable registry and rerun all gates after the governing repository publishes a
  self-consistent manifest containing the intended ontology and packet contracts.

Passing local tests establishes internal consistency only; it does not cure this authority gap.

## Required specification-owner publication

1. Close or supersede PR #1 without merging it.
2. Publish or attach the actual kernel authority as one reviewable unit: the 13-family
   `contracts.manifest.json`, ontology 0.3.0 schema and generated models, all nine additional family
   schemas and digests, and the current verifier with its exact failure output. The available local
   package is evidence/review material only and cannot declare itself governing.
3. Publish the intended ontology lineage and compatibility explicitly. The governing default branch
   currently contains 0.1.0; local 0.2.0 and 0.3.0 must either be published with their migrations and
   compatibility declarations or rejected so provisional implementation can be reconciled.
4. For each local-only family, either publish its exact schema/contract/fixtures/models/compatibility
   and manifest entry or explicitly reject it. Silence cannot be treated as admission.
5. Publish the intended OPEN-019–024 revision. The branch contains 1.0.0 while local implementation
   consumes provisional 1.1.0 bindings.
6. Restore repository CI execution and retain a passing run tied to the admitted commit.

Only after those actions can the implementation registry be synchronized without choosing semantics
or compatibility policy on behalf of the specification owner.
