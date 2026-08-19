export type CompatibilityFinding = {
  path: string;
  rule: string;
  message: string;
  breaking: boolean;
};

type SchemaNode = Record<string, unknown>;

function asObject(value: unknown): SchemaNode {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as SchemaNode)
    : {};
}

/** Conservative reader-compatibility analysis; unknown changes require review. */
export function compareSchemas(
  previous: SchemaNode,
  current: SchemaNode,
): CompatibilityFinding[] {
  const findings: CompatibilityFinding[] = [];

  function walk(oldValue: unknown, newValue: unknown, path: string) {
    if (
      !oldValue ||
      !newValue ||
      typeof oldValue !== "object" ||
      typeof newValue !== "object" ||
      Array.isArray(oldValue) ||
      Array.isArray(newValue)
    ) {
      if (JSON.stringify(oldValue) !== JSON.stringify(newValue)) {
        findings.push({
          path,
          rule: "VALUE_CHANGED",
          message: "schema constraint changed",
          breaking: true,
        });
      }
      return;
    }
    const old = oldValue as SchemaNode;
    const next = newValue as SchemaNode;
    const oldRequired = new Set((old.required as string[] | undefined) ?? []);
    const newRequired = new Set((next.required as string[] | undefined) ?? []);
    for (const name of [...newRequired].filter((n) => !oldRequired.has(n)).sort()) {
      findings.push({
        path: `${path}.required`,
        rule: "REQUIRED_ADDED",
        message: `required property added: ${name}`,
        breaking: true,
      });
    }
    const oldEnum = old.enum;
    const newEnum = next.enum;
    if (Array.isArray(oldEnum) && Array.isArray(newEnum)) {
      const newSet = new Set(newEnum.map(String));
      const removed = oldEnum.map(String).filter((v) => !newSet.has(v));
      if (removed.length) {
        findings.push({
          path: `${path}.enum`,
          rule: "ENUM_NARROWED",
          message: `values removed: ${[...removed].sort().join(",")}`,
          breaking: true,
        });
      }
    }
    if (old.type !== next.type && "type" in old && "type" in next) {
      findings.push({
        path: `${path}.type`,
        rule: "TYPE_CHANGED",
        message: "type changed",
        breaking: true,
      });
    }
    const oldAdditional = old.additionalProperties ?? true;
    const newAdditional = next.additionalProperties ?? true;
    if (oldAdditional === true && newAdditional === false) {
      findings.push({
        path,
        rule: "UNKNOWN_FIELDS_REJECTED",
        message: "additional properties became forbidden",
        breaking: true,
      });
    }
    const oldProps = asObject(old.properties);
    const newProps = asObject(next.properties);
    for (const name of Object.keys(oldProps)
      .filter((n) => !(n in newProps))
      .sort()) {
      findings.push({
        path: `${path}.properties.${name}`,
        rule: "PROPERTY_REMOVED",
        message: "property removed",
        breaking: true,
      });
    }
    for (const name of Object.keys(oldProps)
      .filter((n) => n in newProps)
      .sort()) {
      walk(oldProps[name], newProps[name], `${path}.properties.${name}`);
    }
    const oldDefs = asObject(old.$defs);
    const newDefs = asObject(next.$defs);
    for (const name of Object.keys(oldDefs)
      .filter((n) => !(n in newDefs))
      .sort()) {
      findings.push({
        path: `${path}.$defs.${name}`,
        rule: "DEFINITION_REMOVED",
        message: "definition removed",
        breaking: true,
      });
    }
    for (const name of Object.keys(oldDefs)
      .filter((n) => n in newDefs)
      .sort()) {
      walk(oldDefs[name], newDefs[name], `${path}.$defs.${name}`);
    }
  }

  walk(previous, current, "$");
  return findings;
}
