# Availability and Booking Contract 1.0

`AVAILABILITY-BOOKING.schema.json` is authoritative for calendar-provider ownership, availability policy, slot derivation, booking commands/results, and unknown-outcome reconciliation.

An active `CalendarProviderBinding` is supplied by its declared owner and binds one tenant, provider kind, provider account, calendar, connector grant, and signed capability inventory. Possession of provider credentials does not create authority. Suspended, revoked, stale, cross-tenant, or capability-incompatible bindings fail closed.

An active `AvailabilityPolicy` is supplied by its declared owner. It contains timezone, weekly windows, blackouts, duration, buffers, slot increment, search horizon, SlotSet lifetime, ordering, and exact travel, location, service-zone, and capacity references. The runtime supplies no defaults. Effective intervals are half-open and versions are append-only.

`CalendarSnapshot` is the normalized provider observation and carries the exact binding, watermark, provider version, time range, busy intervals, digest, and evidence. It is not canonical completion evidence for a write.

`SlotSet` is derived by `availability_v1` from one current readiness decision, policy, provider binding, and calendar snapshot. Candidate starts are enumerated at the policy increment inside working windows; blackouts, busy intervals, buffers, travel, location, service-zone, and capacity exclusions are applied; results are ordered by `starts_at_then_location_id_then_slot_id`. Slot IDs and digests are deterministic over bound inputs. A SlotSet expires no later than 900 seconds after derivation and becomes invalid on any bound version or watermark change.

`BookingCommand` binds the exact journey, appointment version where applicable, selected slot and digest, provider watermark/binding, actor, authorization, effect intent, payload digest, idempotency key, and expiry. Book/reschedule require a current SlotSet and selected slot. Cancel requires an existing appointment and may omit slot fields. Habitat must re-read current authority and versions before issuing a single-use permit; the connector must redeem it before changing the provider.

`BookingResult` distinguishes intent, provider acceptance, confirmation, cancellation, stale/conflict rejection, failure, and unknown outcome. Timeout or ambiguous provider response is `unknown_outcome` and cannot be retried as a new effect until `BookingReconciliation` observes provider truth. Reconciliation uses `booking_reconciliation_v1`, binds the provider observation and prior result, and produces a source-linked terminal result or remains explicitly unknown/conflicted.

Acceptance requires schema fixtures, generated models, deterministic slot tests across DST and timezones, stale watermark/slot/version rejection, revoked binding and authority races, duplicate/idempotency tests, provider timeout and reconciliation tests, concurrent booking serialization, and reconstruction from command through provider receipt to canonical appointment state.

The executable acceptance binding is `src/buyer_ops_contracts/contract_acceptance.py`; fixtures and deterministic tests are in `tests/fixtures/availability_booking` and `tests/test_new_contract_acceptance.py`. Canonical digests use the qualification contract serialization. A slot digest excludes `slotId` and `slotDigest`; `slotId` is the lowercase hex portion of that digest. Ambiguous local boundaries select `fold=0`; nonexistent local boundaries yield no window. Command admission rechecks active authority, active provider binding, tenant, provider watermark, expiry, and expected appointment version. Production writer and provider-effect activation remain blocked as recorded in `AVAILABILITY-BOOKING-COMPATIBILITY.json`.
