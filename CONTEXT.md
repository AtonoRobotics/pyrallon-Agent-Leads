# Buyer Operations

This context describes the canonical language for operating a residential buyer journey while preserving evidence, authority, and temporal history.

## Language

**Canonical record**:
A tenant-scoped, versioned fact or operational object admitted under the executable ontology and retained with its effective interval and provenance.
_Avoid_: Row, object, entity

**Value object**:
A schema fragment that has no independent canonical identity or lifecycle and exists only inside a canonical record.
_Avoid_: Record type, entity

**Buying party**:
One or more people participating in a single purchase decision, with explicit roles and decision authority.
_Avoid_: Buyer account, household

**Buyer journey**:
One purchase objective pursued by a buying party over time; the same buying party may have multiple distinct journeys.
_Avoid_: Funnel, deal

**Epistemic item**:
The shared conceptual category for Evidence, Assertion, Verified Fact, Inference, and Memory; it is not itself an executable canonical record type.
_Avoid_: Fact, knowledge blob

**Evidence**:
A source observation or immutable artifact reference captured with provenance and retention metadata.
_Avoid_: Fact, assertion

**Assertion**:
An attributed proposition that has not passed a predicate-specific verification rule.
_Avoid_: Verified fact

**Verified fact**:
An evidence-backed proposition admitted through a named, predicate-specific verification rule.
_Avoid_: Assertion, model conclusion

**Inference**:
A time-bounded proposition derived from declared inputs by a versioned method or model.
_Avoid_: Fact

**Memory**:
A source-linked recall aid or compaction that cannot independently establish consent, authority, representation, or completion.
_Avoid_: Fact, source of truth

**Supersession**:
An atomic replacement that closes the prior record's effective interval and links the successor without erasing history.
_Avoid_: Overwrite

**Operator command**:
A short-lived, version-bound request by an authenticated actor to apply a governed mutation; it is neither canonical evidence nor continuing authority.
_Avoid_: Admin action, direct edit

**Canonical mutation payload**:
The complete ontology-valid record set proposed by an operator command, including its own attribution and evidence rather than server-invented canonical values.
_Avoid_: Patch, command metadata

**Consultation readiness**:
A versioned deterministic decision derived from current journey records and an owner-supplied qualification policy; cognitive output may propose it but cannot establish it.
_Avoid_: Lead score, funnel stage

**Slot set**:
A short-lived, privacy-safe set of consultation slots derived from one versioned calendar snapshot and one owner-supplied availability policy.
_Avoid_: Calendar availability, suggested times
