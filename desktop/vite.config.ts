import { defineConfig } from "vite";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, relative, isAbsolute } from "node:path";
const output = resolve(
  process.env.AGENT_HELM_WEB_DIST ||
    mkdtempSync(join(tmpdir(), "agent-helm-web-")),
);
const rel = relative(resolve(".."), output);
if (!rel || (!rel.startsWith("..") && !isAbsolute(rel)))
  throw Error("Frontend build output must remain outside source");
export default defineConfig({
  server: { host: "127.0.0.1", port: 1420, strictPort: true },
  build: { outDir: output, emptyOutDir: true },
});
