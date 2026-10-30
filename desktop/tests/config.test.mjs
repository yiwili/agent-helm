import test from "node:test";
import assert from "node:assert/strict";
import { win32 } from "node:path";
import { frontendDirectory } from "../scripts/config.mjs";

test("same-volume frontend is a relative filesystem path, not a drive URL", () => {
  const source = String.raw`D:\checkout\desktop\src-tauri`;
  const target = String.raw`D:\external builds\界面\web`;
  const actual = frontendDirectory(target, source);
  assert.equal(win32.resolve(source, actual), target);
  assert.throws(() => new URL(actual));
});

test("different-volume output stays a filesystem path without drive URL ambiguity", () => {
  const source = String.raw`D:\checkout\desktop\src-tauri`;
  const target = String.raw`C:\external builds\web`;
  const actual = frontendDirectory(target, source);
  assert.equal(actual, win32.toNamespacedPath(target));
  assert.throws(() => new URL(actual));
});

test("UNC output remains a namespaced file directory", () => {
  const actual = frontendDirectory(String.raw`\\server\share\web`, String.raw`D:\checkout`);
  assert.equal(actual, String.raw`\\?\UNC\server\share\web`);
  assert.throws(() => new URL(actual));
});
