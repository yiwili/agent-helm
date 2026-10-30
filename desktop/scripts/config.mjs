import { win32, resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const manifest = resolve(dirname(fileURLToPath(import.meta.url)), "../src-tauri");

export function frontendDirectory(directory, manifestDirectory = manifest) {
  // FrontendDist deserializes Url before PathBuf. A bare C:\... is a URL
  // with scheme c, not a directory. Prefer relative paths; for another volume,
  // use the Windows extended-length path prefix, which cannot parse as a URL.
  const absolute = win32.resolve(directory);
  const relative = win32.relative(manifestDirectory, absolute);
  if (win32.isAbsolute(relative)) return win32.toNamespacedPath(absolute);
  return "./" + relative.replaceAll("\\", "/");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (!process.argv[2]) throw Error("Expected external frontend directory");
  console.log(JSON.stringify({ build: { frontendDist: frontendDirectory(process.argv[2]) } }));
}
