# Operator console (PKT-00–PKT-10 in-process)

Texas residential buyer-ops console that implements OT-01 packets against the
hash-pinned ontology and gateway contracts in this repository.

## What this branch contains

In-process implementations of PKT-00 through PKT-10: canonical CRM, evidence
ledger, Habitat permits, form ingress, deterministic qualification, local
slots, and the operator surface.

## What is not activated

- Email, SMS, and calendar have no live grant. Habitat may issue a permit; the
  connector redeems it and refuses the provider call.
- Outbound AI voice is prohibited (GATE-032).
- Workflow is event-sourced in-process. It is not Temporal Cloud.
- Live model cognition is off. Qualification uses the deterministic compiler
  and writes assertions only.

## Run

```bash
npm install
npm test
npm run dev
```
