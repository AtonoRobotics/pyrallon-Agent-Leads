import { pathToFileURL } from "node:url";
import { resolve as resolvePath } from "node:path";

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith("@/")) {
    let rest = specifier.slice(2);
    if (!/\.(ts|tsx|js|mjs|cjs|json)$/.test(rest)) rest += ".ts";
    const file = resolvePath(process.cwd(), "src", rest);
    return nextResolve(pathToFileURL(file).href, context);
  }
  return nextResolve(specifier, context);
}
