import { createHash, randomUUID } from "node:crypto";
import { SCHEMA_VERSION } from "../contracts/violations.ts";

export type ActorRef = {
  actorType: "person" | "license_holder" | "service_principal" | "system_migration";
  actorId: string;
};

export function newId() {
  return randomUUID();
}

export function sha256Digest(value: string) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

export function iso(date?: Date | null) {
  return (date ?? new Date()).toISOString();
}

export function canonicalEnvelope(input: {
  id?: string;
  tenantId: string;
  recordType: string;
  status: string;
  createdBy: ActorRef;
  sourceEvidenceIds: string[];
  now?: Date;
}) {
  const now = iso(input.now);
  return {
    id: input.id ?? newId(),
    tenantId: input.tenantId,
    schemaVersion: SCHEMA_VERSION,
    recordType: input.recordType,
    version: 1,
    createdAt: now,
    updatedAt: now,
    effectiveFrom: now,
    createdBy: input.createdBy,
    sourceEvidenceIds: input.sourceEvidenceIds,
    status: input.status,
  };
}
