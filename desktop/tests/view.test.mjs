import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
const source = readFileSync(new URL("../src/view.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
  },
}).outputText;
const { escape, stateLabel } = await import(
  "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
test("untrusted provider content cannot inject markup or attributes", () => {
  assert.equal(
    escape('<img src=x onerror="run()">&\''),
    "&lt;img src=x onerror=&quot;run()&quot;&gt;&amp;&#39;",
  );
});
test("unknown lifecycle states stay visible", () => {
  assert.equal(stateLabel("reviewing"), "待人工审查");
  assert.equal(
    stateLabel("reconciliation_required"),
    "reconciliation_required",
  );
});
