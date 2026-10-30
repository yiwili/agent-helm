import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
import { JSDOM } from "jsdom";

test("desktop forms persist worker model and approve the exact displayed snapshot", async () => {
  const dom = new JSDOM('<div id="app"></div>', { url: "http://localhost" });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.FormData = dom.window.FormData;
  globalThis.__noTimer = () => 0;
  const calls = [];
  let profile = {
    provider: "codex",
    name: "Codex",
    enabled: true,
    connection: "auto",
    executable: "",
    model: "fixture-old",
    models: ["fixture-old"],
    budget_ledger: "",
  };
  const proposal = {
    proposal_id: "proposal-fixture",
    plan_hash: "plan-fixture-hash",
    runtime_hash: "runtime-fixture-hash",
    plan: { goal: "synthetic" },
    agent_configuration: { worker_models: { codex: "fixture-new" } },
  };
  globalThis.__bridge = {
    connected: true,
    request: async (action, payload = {}) => {
      calls.push({ action, payload });
      if (action === "dashboard")
        return {
          agents: [profile],
          runs: [
            {
              goal: "synthetic",
              provider: "codex",
              run_id: "run-fixture",
              state: "running",
            },
          ],
          probes: {},
          active_runs: 0,
          state_root: "external",
        };
      if (action === "save_agent") {
        profile = payload.profile;
        return {};
      }
      if (action === "create_task")
        return { proposal_id: proposal.proposal_id };
      if (action === "proposal") return proposal;
      if (action === "approve_start") return { run_id: "run-fixture" };
      if (action === "run_detail")
        return {
          status: { run_id: "run-fixture", state: "running", tasks: [] },
          events: [],
          evidence: [],
        };
      throw Error("Unexpected action " + action);
    },
  };
  const view = ts.transpileModule(
    readFileSync(new URL("../src/view.ts", import.meta.url), "utf8"),
    { compilerOptions: { module: ts.ModuleKind.ESNext } },
  ).outputText;
  let source = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8")
    .replace(/import\s+["']\.\/style\.css["'];/, "")
    .replace(
      /import\s+\{ connected, request \}\s+from ["']\.\/bridge["'];/,
      "const {connected,request}=globalThis.__bridge;",
    )
    .replace(
      /from ["']\.\/view["']/,
      `from 'data:text/javascript;base64,${Buffer.from(view).toString("base64")}'`,
    );
  source = source.replace(/\bsetInterval\s*\(/g, "globalThis.__noTimer("); // No timers or desktop apps in this test.
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  }).outputText;
  await import(
    "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
  );
  const settle = () => new Promise((r) => setTimeout(r, 0));
  await settle();
  document.querySelector("[data-action=detail]").click();
  await settle();
  assert.ok(
    calls.some((c) => c.action === "run_detail"),
    "task row buttons must open evidence",
  );
  document.querySelector("[data-tab=agents]").click();
  document.querySelector("[name=custom_model]").value = "fixture-new";
  document
    .querySelector("form.agent")
    .dispatchEvent(
      new dom.window.Event("submit", { bubbles: true, cancelable: true }),
    );
  await settle();
  assert.equal(
    calls.find((c) => c.action === "save_agent").payload.profile.model,
    "fixture-new",
  );
  document.querySelector("[data-tab=tasks]").click();
  document
    .querySelector("#task-form")
    .dispatchEvent(
      new dom.window.Event("submit", { bubbles: true, cancelable: true }),
    );
  await settle();
  assert.equal(
    calls.some((c) => c.action === "approve_start"),
    false,
    "creating a plan cannot start inference",
  );
  assert.match(
    document.querySelector("[role=dialog]").textContent,
    /runtime-fixture-hash/,
  );
  document.querySelector("#approve-form [name=confirmed]").checked = true;
  document
    .querySelector("#approve-form")
    .dispatchEvent(
      new dom.window.Event("submit", { bubbles: true, cancelable: true }),
    );
  await settle();
  assert.deepEqual(calls.find((c) => c.action === "approve_start").payload, {
    proposal_id: proposal.proposal_id,
    plan_hash: proposal.plan_hash,
    runtime_hash: proposal.runtime_hash,
    confirmed: true,
  });
  assert.equal(
    calls.some(
      (c) => c.action === "budget_create" || c.action === "probe_agent",
    ),
    false,
  );
  dom.window.close();
});
