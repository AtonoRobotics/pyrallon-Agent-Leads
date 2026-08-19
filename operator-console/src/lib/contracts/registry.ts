import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import type { ErrorObject, ValidateFunction } from "ajv";
import ontologySchema from "./ontology.schema.json" with { type: "json" };
import gatewaySchema from "./gateway.schema.json" with { type: "json" };
import manifestJson from "./contracts.manifest.json" with { type: "json" };
import { ContractViolation } from "./violations.ts";
import type { JsonRecord } from "./semantic.ts";

export type ContractName = "ontology" | "gateway";

type ManifestEntry = {
  name: ContractName;
  resource: string;
  schemaId: string;
  schemaVersion: string;
  sha256: string;
};

type Manifest = { manifestVersion: string; contracts: ManifestEntry[] };

export type RegisteredContract = {
  name: ContractName;
  schemaId: string;
  sha256: string;
  schema: object;
  validate: ValidateFunction;
};

const dir = dirname(fileURLToPath(import.meta.url));

const PACKAGED: Record<ContractName, object> = {
  ontology: ontologySchema as object,
  gateway: gatewaySchema as object,
};

const ajv = new Ajv2020({
  allErrors: true,
  strict: false,
  validateFormats: true,
});

ajv.addFormat("date-time", {
  type: "string",
  validate: (value: string) =>
    !Number.isNaN(Date.parse(value)) && /[zZ]|[+-]\d{2}:\d{2}$/.test(value),
});

function loadBytes(resource: string): Buffer | null {
  try {
    return readFileSync(join(dir, resource));
  } catch {
    return null;
  }
}

function loadRegistered(): Record<ContractName, RegisteredContract> {
  const manifest = (manifestJson as Manifest);
  const contracts = {} as Record<ContractName, RegisteredContract>;
  for (const entry of manifest.contracts ?? []) {
    const raw = loadBytes(entry.resource);
    if (raw) {
      const digest = createHash("sha256").update(raw).digest("hex");
      if (digest !== entry.sha256) {
        throw new Error(`contract digest mismatch: ${entry.name}`);
      }
    }
    const schema = structuredClone(PACKAGED[entry.name]) as Record<string, unknown>;
    if (entry.name === "ontology") applyPersonAllOfFix(schema);
    contracts[entry.name] = {
      name: entry.name,
      schemaId: entry.schemaId,
      sha256: entry.sha256,
      schema,
      validate: ajv.compile(schema),
    };
  }
  return contracts;
}

export const registry = loadRegistered();

/**
 * Governing Person schema puts `additionalProperties: false` on the type-specific
 * allOf branch, which does not list CanonicalFields. That rejects the envelope
 * (id, tenantId, …) before unevaluatedProperties can accept them. Other record
 * types omit that keyword. File bytes stay hash-pinned; this compile overlay
 * copies CanonicalFields.properties into that branch so unknown fields still
 * fail unevaluatedProperties.
 */
function applyPersonAllOfFix(schema: Record<string, unknown>) {
  const defs = schema.$defs as Record<string, Record<string, unknown>> | undefined;
  if (!defs?.Person || !defs.CanonicalFields) return;
  const canonicalProps = (defs.CanonicalFields.properties ?? {}) as Record<string, unknown>;
  const person = defs.Person;
  const allOf = person.allOf as Array<Record<string, unknown>> | undefined;
  if (!Array.isArray(allOf)) return;
  for (const branch of allOf) {
    if (branch.additionalProperties === false && branch.properties) {
      branch.properties = {
        ...canonicalProps,
        ...(branch.properties as Record<string, unknown>),
      };
    }
  }
}

export function validateRecord(record: JsonRecord, contract: ContractName): void {
  const registered = registry[contract];
  if (!registered) throw new Error(`unsupported contract: ${contract}`);
  const ok = registered.validate(record);
  if (ok) return;
  const errors = (registered.validate.errors ?? []) as ErrorObject[];
  throw new ContractViolation(
    errors.map((err) => ({
      code: "STRUCTURAL_SCHEMA",
      path: err.instancePath || "$",
      message: err.message ?? "failed schema",
    })),
  );
}
