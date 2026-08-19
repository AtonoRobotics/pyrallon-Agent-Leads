import { validateRecord } from "./registry.ts";
import { defaultSemanticPolicy, validateSemantics, type JsonRecord } from "./semantic.ts";
import { ContractViolation, SCHEMA_VERSION } from "./violations.ts";

export function validateOntologyRecord(record: JsonRecord): void {
  if (record.schemaVersion !== SCHEMA_VERSION) {
    throw new ContractViolation([
      {
        code: "UNSUPPORTED_SCHEMA_VERSION",
        path: "$.schemaVersion",
        message: `expected ${SCHEMA_VERSION}`,
      },
    ]);
  }
  validateRecord(record, "ontology");
}

export function admitOntologyRecord(record: JsonRecord, now = new Date()): void {
  validateOntologyRecord(record);
  validateSemantics(record, defaultSemanticPolicy(now));
}

export function validateGatewayRecord(record: JsonRecord): void {
  validateRecord(record, "gateway");
}
