import { ContractViolation, type Violation } from "./violations.ts";

export type JsonRecord = Record<string, unknown>;

export type SemanticPolicy = {
  now: Date;
  maxProposalTtlMs: Record<string, number>;
  supportedProposalVersions: ReadonlySet<string>;
  clockSkewMs: number;
};

export function defaultSemanticPolicy(now = new Date()): SemanticPolicy {
  return {
    now,
    maxProposalTtlMs: {},
    supportedProposalVersions: new Set(["cognitive-proposal/1.1.0"]),
    clockSkewMs: 30_000,
  };
}

function parseTime(value: string): Date {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) throw new Error("invalid timestamp");
  if (!/[zZ]|[+-]\d{2}:\d{2}$/.test(value)) {
    throw new Error("timestamp must include an offset");
  }
  return parsed;
}

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

export function validateSemantics(
  record: JsonRecord,
  policy: SemanticPolicy = defaultSemanticPolicy(),
): void {
  const violations: Violation[] = [];
  const recordType = record.recordType;

  const ordered = (before: string, after: string, strict = false) => {
    if (!(before in record) || !(after in record)) return;
    const left = parseTime(String(record[before]));
    const right = parseTime(String(record[after]));
    const valid = strict ? left < right : left <= right;
    if (!valid) {
      violations.push({
        code: "TEMPORAL_ORDER",
        path: `$.${after}`,
        message: `must follow ${before}`,
      });
    }
  };

  ordered("createdAt", "updatedAt");
  ordered("effectiveFrom", "effectiveTo");
  ordered("proposedAt", "expiresAt", true);
  ordered("evaluatedAt", "expiresAt", true);
  ordered("startsAt", "endsAt", true);
  ordered("effectiveAt", "terminatesAt", true);

  if (recordType === "CognitiveWorkRequest") {
    const packet = asRecord(record.contextPacket);
    if (record.contextManifestId !== packet.manifestId) {
      violations.push({
        code: "CONTEXT_MANIFEST_MISMATCH",
        path: "$.contextManifestId",
        message: "must equal contextPacket.manifestId",
      });
    }
    if (!policy.supportedProposalVersions.has(String(record.requiredProposalSchemaVersion))) {
      violations.push({
        code: "UNSUPPORTED_PROPOSAL_VERSION",
        path: "$.requiredProposalSchemaVersion",
        message: "is not supported",
      });
    }
    if (parseTime(String(packet.compiledAt)) >= parseTime(String(packet.expiresAt))) {
      violations.push({
        code: "CONTEXT_EXPIRED_AT_COMPILE",
        path: "$.contextPacket.expiresAt",
        message: "must follow compiledAt",
      });
    }
  }

  if (recordType === "CognitiveProposal") {
    const claims = Array.isArray(record.claims) ? record.claims : [];
    const claimIds = claims.map((c) => String(asRecord(c).claimId));
    if (new Set(claimIds).size !== claimIds.length) {
      violations.push({
        code: "DUPLICATE_CLAIM_ID",
        path: "$.claims",
        message: "claimId values must be unique",
      });
    }
    const actions = Array.isArray(record.proposedActions) ? record.proposedActions : [];
    const proposalIds = actions.map((a) => String(asRecord(a).proposalId));
    if (new Set(proposalIds).size !== proposalIds.length) {
      violations.push({
        code: "DUPLICATE_ACTION_ID",
        path: "$.proposedActions",
        message: "proposalId values must be unique",
      });
    }
    const known = new Set(claimIds);
    const proposalExpiry = parseTime(String(record.expiresAt));
    actions.forEach((raw, index) => {
      const action = asRecord(raw);
      const sources = Array.isArray(action.sourceClaimIds)
        ? action.sourceClaimIds.map(String)
        : [];
      const unknown = sources.filter((id) => !known.has(id));
      if (unknown.length) {
        violations.push({
          code: "UNRESOLVED_SOURCE_CLAIM",
          path: `$.proposedActions.${index}.sourceClaimIds`,
          message: `unknown claims: ${unknown.sort().join(",")}`,
        });
      }
      const window = asRecord(action.requestedExecutionWindow);
      if (parseTime(String(window.notBefore)) >= parseTime(String(window.expiresAt))) {
        violations.push({
          code: "INVALID_EXECUTION_WINDOW",
          path: `$.proposedActions.${index}.requestedExecutionWindow`,
          message: "notBefore must precede expiresAt",
        });
      }
      if (parseTime(String(window.expiresAt)) > proposalExpiry) {
        violations.push({
          code: "ACTION_OUTLIVES_PROPOSAL",
          path: `$.proposedActions.${index}.requestedExecutionWindow.expiresAt`,
          message: "must not exceed proposal expiry",
        });
      }
    });
    const ttl = policy.maxProposalTtlMs[String(record.actionClass)];
    if (
      ttl != null &&
      proposalExpiry.getTime() - parseTime(String(record.proposedAt)).getTime() > ttl
    ) {
      violations.push({
        code: "PROPOSAL_TTL_EXCEEDED",
        path: "$.expiresAt",
        message: "exceeds action-class policy",
      });
    }
    if (proposalExpiry.getTime() <= policy.now.getTime() - policy.clockSkewMs) {
      violations.push({
        code: "STALE_PROPOSAL",
        path: "$.expiresAt",
        message: "proposal is expired",
      });
    }
  }

  if (recordType === "WrittenBuyerAgreement") {
    const effective = parseTime(String(record.effectiveAt));
    const terminates = parseTime(String(record.terminatesAt));
    if (record.agreementType === "non_representation_showing") {
      if (terminates.getTime() - effective.getTime() > 14 * 24 * 3600_000) {
        violations.push({
          code: "NON_REP_TERM_EXCEEDED",
          path: "$.terminatesAt",
          message: "showing-only agreement may not exceed 14 days",
        });
      }
      const services = Array.isArray(record.serviceDefinitions)
        ? Object.fromEntries(
            record.serviceDefinitions.map((item) => {
              const row = asRecord(item);
              return [String(row.serviceCode), Boolean(row.allowed)];
            }),
          )
        : {};
      const extraAllowed = Object.entries(services).some(
        ([key, allowed]) => key !== "showing_access" && allowed,
      );
      if (services.showing_access !== true || extraAllowed) {
        violations.push({
          code: "NON_REP_SERVICE_SCOPE",
          path: "$.serviceDefinitions",
          message: "only showing_access may be allowed",
        });
      }
    }
    if (record.executionState === "executed" || record.executionState === "effective") {
      const compensation = asRecord(record.compensation);
      if (!compensation.objectivelyAscertainable || !compensation.negotiabilityDisclosurePresent) {
        violations.push({
          code: "INVALID_COMPENSATION_DISCLOSURE",
          path: "$.compensation",
          message:
            "effective agreement requires ascertainable compensation and negotiability disclosure",
        });
      }
    }
  }

  if (recordType === "AgreementQualification") {
    const hasAgreement = "agreementId" in record;
    const hasException = "exceptionCode" in record;
    if (record.result === "qualified" && hasAgreement === hasException) {
      violations.push({
        code: "QUALIFICATION_BASIS",
        path: "$",
        message: "qualified result requires exactly one agreement or approved exception",
      });
    }
  }

  if (violations.length) throw new ContractViolation(violations);
}

export function validateGatewayPair(
  request: JsonRecord,
  proposal: JsonRecord,
  policy: SemanticPolicy = defaultSemanticPolicy(),
): void {
  validateSemantics(request, policy);
  validateSemantics(proposal, policy);
  const violations: Violation[] = [];
  for (const fieldName of ["workId", "actionClass", "contextManifestId"] as const) {
    if (request[fieldName] !== proposal[fieldName]) {
      violations.push({
        code: "REQUEST_PROPOSAL_MISMATCH",
        path: `$.${fieldName}`,
        message: "proposal does not match request",
      });
    }
  }
  if (request.requiredProposalSchemaVersion !== proposal.schemaVersion) {
    violations.push({
      code: "PROPOSAL_VERSION_MISMATCH",
      path: "$.schemaVersion",
      message: "does not satisfy request",
    });
  }
  if (violations.length) throw new ContractViolation(violations);
}
