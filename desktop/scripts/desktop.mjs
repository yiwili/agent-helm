import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join, relative, isAbsolute, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { frontendDirectory } from "./config.mjs";
const desktop = resolve(dirname(fileURLToPath(import.meta.url)), ".."),
  repo = resolve(desktop, "..");
const mode = process.argv[2];
if (!["dev", "build"].includes(mode)) throw Error("Use dev or build");
if (process.platform !== "win32")
  throw Error(
    "This delivery currently targets Windows. Linux/macOS validation is deferred.",
  );
const root = process.env.AGENT_HELM_DESKTOP_OUTPUT
  ? resolve(process.env.AGENT_HELM_DESKTOP_OUTPUT)
  : mkdtempSync(join(tmpdir(), "agent-helm-desktop-"));
const rel = relative(repo, root);
if (
  !rel ||
  (!rel.startsWith("..") && !isAbsolute(rel)) ||
  !relative(root, repo).startsWith("..")
)
  throw Error("Build/state output must be outside source");
mkdirSync(root, { recursive: true });
const env = {
  ...process.env,
  AGENT_HELM_WEB_DIST: join(root, "web"),
  CARGO_TARGET_DIR: join(root, "cargo"),
  AGENT_HELM_SOURCE_ROOT: repo,
};
const bin = (name) =>
  join(
    desktop,
    "node_modules",
    name === "vite" ? "vite/bin/vite.js" : "@tauri-apps/cli/tauri.js",
  );
function run(command, args) {
  const r = spawnSync(process.execPath, [command, ...args], {
    cwd: desktop,
    env,
    stdio: "inherit",
    shell: false,
  });
  if (r.status !== 0) throw Error(`Command failed (${r.status}): ${command}`);
}
const config = { build: { frontendDist: frontendDirectory(env.AGENT_HELM_WEB_DIST) } };
if (mode === "build") {
  let engine =
    process.env.AGENT_HELM_ENGINE_DIR &&
    resolve(process.env.AGENT_HELM_ENGINE_DIR);
  if (!engine) {
    const python = env.AGENT_HELM_PYTHON || "python";
    const result = spawnSync(
      python,
      [
        join(repo, "scripts", "build.py"),
        "--build-only",
        "--output",
        join(root, "core"),
      ],
      { cwd: repo, env, stdio: "inherit", shell: false },
    );
    if (result.status !== 0)
      throw Error(
        "Backend build failed; set AGENT_HELM_PYTHON to Python 3.11+",
      );
    engine = join(root, "core", "dist", "agent-helm");
  }
  if (
    !engine ||
    !existsSync(join(engine, "agent-helm.exe")) ||
    !existsSync(join(engine, "_internal"))
  )
    throw Error(
      "Set AGENT_HELM_ENGINE_DIR to a freshly built onedir backend (agent-helm.exe + _internal).",
    );
  // Resource-map folder syntax copies the complete onedir including the Windows supervisor helpers.
  config.bundle = { resources: { [engine + "/"]: "engine/" } };
  run(bin("vite"), ["build"]);
} else if (!env.AGENT_HELM_PYTHON)
  throw Error("Set AGENT_HELM_PYTHON to an absolute Python 3.11+ executable.");
const conf = join(root, "tauri.generated.json");
writeFileSync(conf, JSON.stringify(config, null, 2));
console.log(`External output: ${root}`);
if (mode === "build") {
  run(bin("tauri"), ["build", "--config", conf]);
  console.log(
    "Build finished. Packaged application was not launched or validated.",
  );
} else {
  const vite = spawn(process.execPath, [bin("vite")], {
    cwd: desktop,
    env,
    stdio: "inherit",
    shell: false,
  });
  const shutdown = () => {
    if (vite.pid)
      spawnSync("taskkill.exe", ["/PID", String(vite.pid), "/T", "/F"], {
        stdio: "ignore",
      });
  };
  process.once("exit", shutdown);
  process.once("SIGINT", () => process.exit(130));
  try {
    let ready = false;
    for (let n = 0; n < 100; n++) {
      try {
        const r = await fetch("http://127.0.0.1:1420");
        if (r.ok) {
          ready = true;
          break;
        }
      } catch {}
      await new Promise((r) => setTimeout(r, 100));
    }
    if (!ready) throw Error("Development server did not start");
    run(bin("tauri"), ["dev", "--config", conf]);
  } finally {
    shutdown();
  }
}
