import { CONNECTOR_INVENTORY } from "@/lib/connectors/gateway.ts";

export type PacketStatus = "complete" | "complete_in_process" | "fail_closed" | "blocked";

export type PacketRecord = {
  id: string;
  name: string;
  status: PacketStatus;
  summary: string;
  evidence: string[];
};

export const PACKETS: PacketRecord[] = [
  {
    id: "PKT-00",
    name: "Hash-pinned contracts",
    status: "complete",
    summary: "Ontology buyer-ops/0.1.0 and gateway 1.1.0 are SHA-256 pinned. Ajv 2020-12 + semantic.py port admit or reject before mutation.",
    evidence: ["PKT-00-HASH", "STRUCTURAL_SCHEMA", "NON_REP_TERM_EXCEEDED"],
  },
  {
    id: "PKT-01",
    name: "Canonical CRM",
    status: "complete",
    summary: "Person, party, journey, consent, suppression, observations, appointments. Tenant isolation, optimistic versioning, reconstruction, endpoint uniqueness.",
    evidence: ["TENANT_ISOLATION", "VERSION_CONFLICT", "RECONSTRUCTION"],
  },
  {
    id: "PKT-02",
    name: "Evidence ledger",
    status: "complete",
    summary: "Append-only hash chain with prev_hash, entry_hash, artifact_digest, checkpoints, and tombstones. Tamper fails closed.",
    evidence: ["EVIDENCE_CHAIN", "EVIDENCE_TAMPER", "ARTIFACT_DIGEST_MISMATCH"],
  },
  {
    id: "PKT-03",
    name: "Habitat (in-process)",
    status: "complete_in_process",
    summary: "EffectIntent → evaluate → single-use permit. Replay, payload mutation, suppression, IABS, showing vs offer, GATE-032. Not a hosted Habitat cluster.",
    evidence: ["PERMIT_REPLAY", "PAYLOAD_MUTATION", "GATE-032", "SHOWING_VS_OFFER"],
  },
  {
    id: "PKT-04",
    name: "Durable workflow",
    status: "complete_in_process",
    summary: "Event-sourced BuyerJourneyWorkflow with replay/fold, duplicate signal suppression, leases, and timers. This is not Temporal Cloud.",
    evidence: ["WORKFLOW_REPLAY", "DUPLICATE_SIGNAL", "TEMPORAL-CLOUD"],
  },
  {
    id: "PKT-05",
    name: "Form ingress",
    status: "complete",
    summary: "ot01.inbound/1. Form admits. Email/SMS fail closed without an active grant and verified signature. STOP writes Suppression. Inbound events are idempotent.",
    evidence: ["FORM_ADMIT", "EMAIL_INGRESS_DENIED", "INBOUND_IDEMPOTENCY", "STOP_SUPPRESSION"],
  },
  {
    id: "PKT-06",
    name: "Connectors",
    status: "fail_closed",
    summary: "form.local is this app. Email, SMS, and calendar have no live grant. Voice is prohibited. Permits redeem before the inactive-grant rejection so no provider call is made.",
    evidence: ["CONNECTOR_INACTIVE", "PERMIT_REDEEM_THEN_INACTIVE", "GATE-032"],
  },
  {
    id: "PKT-07",
    name: "Context compiler",
    status: "complete",
    summary: "Canonical-grounded CognitiveWorkRequest. Live model routes stay blocked without GATE-013 evidence.",
    evidence: ["CONTEXT_PACKET", "LIVE-COGNITION"],
  },
  {
    id: "PKT-08",
    name: "Qualification proposals",
    status: "complete",
    summary: "Deterministic CognitiveProposal. Allowed OT-01 actions only. Claims are assertions. Apply refuses verified_fact. Protected-class language is rejected.",
    evidence: ["PROPOSAL_SCHEMA", "ILLEGAL_MODEL_FACT", "PROHIBITED_PROXY", "PROHIBITED_COGNITIVE_ACTION"],
  },
  {
    id: "PKT-09",
    name: "Local slots",
    status: "fail_closed",
    summary: "Local policy slots and proposed Appointments. Confirm requires Habitat calendar_write plus an active calendar grant, which does not exist.",
    evidence: ["LOCAL_SLOT", "LIVE-CALENDAR"],
  },
  {
    id: "PKT-10",
    name: "Operator surface",
    status: "complete",
    summary: "Pipeline, exceptions, consults, capture, and this packet board read canonical state. Activation flags are inventory, not theater.",
    evidence: ["OPERATOR_SURFACE"],
  },
];

export function connectorInventoryView() {
  return CONNECTOR_INVENTORY.map((c) => ({
    connectorId: c.connectorId,
    channel: c.channel,
    status: c.status,
    notes: c.notes,
  }));
}

export const ACTIVATION = {
  habitat: "in_process" as const,
  temporal: "not_temporal_cloud" as const,
  cognition: "deterministic_local" as const,
  connectors: {
    form: "active" as const,
    email: "inactive" as const,
    sms: "inactive" as const,
    calendar: "inactive" as const,
    voice: "prohibited" as const,
  },
};
