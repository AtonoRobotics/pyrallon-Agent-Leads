import { PGlite } from "@electric-sql/pglite";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { Sql } from "../db.ts";

export async function openPacketSql(): Promise<{ sql: Sql; close: () => Promise<void> }> {
  const pg = new PGlite();
  await pg.waitReady;
  const dir = join(process.cwd(), "migrations");
  for (const name of readdirSync(dir).sort()) {
    if (!name.endsWith(".sql")) continue;
    await pg.exec(readFileSync(join(dir, name), "utf8"));
  }
  const sql = (async <T = Record<string, unknown>>(
    strings: TemplateStringsArray,
    ...values: unknown[]
  ): Promise<T[]> => {
    let text = strings[0];
    for (let i = 0; i < values.length; i += 1) text += `$${i + 1}${strings[i + 1]}`;
    const res = await pg.query<T>(text, values);
    return res.rows;
  }) as Sql;
  sql.query = async <T = Record<string, unknown>>(text: string, params: unknown[] = []) => {
    const res = await pg.query<T>(text, params);
    return res.rows;
  };
  return {
    sql,
    close: async () => {
      await pg.close();
    },
  };
}
