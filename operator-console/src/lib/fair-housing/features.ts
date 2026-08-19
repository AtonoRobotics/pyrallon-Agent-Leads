import { ContractViolation } from "@/lib/contracts/violations.ts";

export const ALLOWED_CRITERIA = new Set([
  "identity",
  "representation",
  "purchase_intent",
  "geography",
  "property",
  "timing",
  "budget_financing",
  "contingency",
  "decision_participants",
  "scheduling",
  "channel",
]);

const PROHIBITED = [
  "race",
  "color",
  "religion",
  "sex",
  "gender",
  "national_origin",
  "disability",
  "familial_status",
  "familial",
  "children",
  "pregnant",
  "steering",
];

export function assertAllowedCriterion(criterionId: string) {
  const key = criterionId.toLowerCase().replaceAll("-", "_");
  if (PROHIBITED.some((p) => key.includes(p))) {
    throw new ContractViolation([
      {
        code: "PROHIBITED_PROXY",
        path: "$.criterionId",
        message: "protected trait or prohibited proxy cannot be a qualification feature",
      },
    ]);
  }
  if (!ALLOWED_CRITERIA.has(criterionId)) {
    throw new ContractViolation([
      {
        code: "CRITERION_NOT_ALLOWLISTED",
        path: "$.criterionId",
        message: `${criterionId} is not an allowed operational feature`,
      },
    ]);
  }
}

export function assertNoProtectedInfluence(text: string) {
  const lower = text.toLowerCase();
  if (PROHIBITED.some((p) => lower.includes(p))) {
    throw new ContractViolation([
      {
        code: "PROHIBITED_PROXY",
        path: "$.text",
        message: "protected trait language cannot influence service, cadence, or ranking",
      },
    ]);
  }
}
