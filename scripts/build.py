"""One-command native build. Build tools and all artifacts stay outside source."""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import venv
import zipfile
import urllib.request
import shutil
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
PYINSTALLER = "6.22.3"
PIP = "25.3"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New external build directory; defaults to a unique temporary directory")
    parser.add_argument("--build-only", action="store_true", help="Create an unverified onedir engine for a desktop bundle; do not launch, smoke-test or release it")
    args = parser.parse_args()
    root = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix="agent-helm-build-"))
    if root.is_relative_to(REPOSITORY) or REPOSITORY.is_relative_to(root):
        raise SystemExit("Build output must be outside the source repository")
    if args.output:
        root.mkdir(parents=True, exist_ok=False)
    build_python = root/"venv"/("Scripts/python.exe" if os.name == "nt" else "bin/python")
    venv.EnvBuilder(with_pip=False).create(root/"venv")
    # Minimal Linux Python installations often lack ensurepip. Bootstrap a pinned
    # universal pip wheel from PyPI, checking its published digest, only in this venv.
    with urllib.request.urlopen(f"https://pypi.org/pypi/pip/{PIP}/json", timeout=30) as response:
        metadata = json.load(response)
    wheel = next(item for item in metadata["urls"] if item["filename"].endswith("py3-none-any.whl"))
    wheel_path = root/wheel["filename"]
    urllib.request.urlretrieve(wheel["url"], wheel_path)
    if hashlib.sha256(wheel_path.read_bytes()).hexdigest() != wheel["digests"]["sha256"]:
        raise SystemExit("pip wheel digest mismatch")
    bootstrap = "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('pip',run_name='__main__')"
    subprocess.run([str(build_python), "-c", bootstrap, str(wheel_path), "--isolated", "install", "--index-url", "https://pypi.org/simple", f"pip=={PIP}", f"pyinstaller=={PYINSTALLER}"], check=True)
    # Onedir avoids the extra Windows onefile bootloader process before Job assignment.
    command = [str(build_python), "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--name", "agent-helm",
               "--paths", str(REPOSITORY/"src"), "--distpath", str(root/"dist"), "--workpath", str(root/"work"), "--specpath", str(root/"spec"),
               "--add-data", str(REPOSITORY/"src/agent_orch/verifier.py")+os.pathsep+"agent_orch",
               str(REPOSITORY/"scripts/frozen_entry.py")]
    with (root/"build.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, check=True)
    binary = root/"dist/agent-helm"/("agent-helm.exe" if os.name == "nt" else "agent-helm")
    if args.build_only:
        print(json.dumps({"executable": str(binary), "layout": "onedir", "verified": False, "purpose": "desktop bundle input"}))
        return
    subprocess.run([sys.executable, str(REPOSITORY/"scripts/verify_mcp.py"), "--executable", str(binary), "--output", str(root/"verification")], check=True)
    print(json.dumps(package_artifact(root), indent=2))


def package_artifact(root):
    root = Path(root).resolve()
    if root.is_relative_to(REPOSITORY) or REPOSITORY.is_relative_to(root):
        raise ValueError("Distribution must remain outside source")
    output = root/"dist/agent-helm"
    binary = output/("agent-helm.exe" if os.name == "nt" else "agent-helm")
    if not binary.is_file() or not json.loads((root/"verification/report.json").read_text(encoding="utf-8"))["passed"]:
        raise ValueError("Verified native build required before distribution")
    shutil.copy2(REPOSITORY/"docs/mcp-packaging.md", output/"README.md")
    (output/"configs").mkdir(exist_ok=True)
    shutil.copy2(REPOSITORY/"configs/mcp.example.json", output/"configs/mcp.example.json")
    for candidate in (Path(sys.base_prefix)/"LICENSE.txt", Path(sys.base_prefix)/"LICENSE"):
        if candidate.is_file():
            shutil.copy2(candidate, output/"LICENSE-Python.txt")
            break
    version = next(line.split('"')[1] for line in (REPOSITORY/"src/agent_orch/__init__.py").read_text(encoding="utf-8").splitlines() if line.startswith("__version__"))
    manifest = {"version": version, "os": platform.system().lower(), "architecture": platform.machine().lower(),
                "python": platform.python_version(), "pyinstaller": PYINSTALLER, "layout": "onedir", "mcp_smoke_passed": True,
                "files": {str(p.relative_to(root/"dist/agent-helm")).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(output.rglob("*")) if p.is_file() and p != output/"manifest.json"}}
    release = root/"release"
    release.mkdir(exist_ok=True)
    (root/"dist/agent-helm/manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = release/f"agent-helm-{version}-{manifest['os']}-{manifest['architecture']}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in (root/"dist").rglob("*"):
            if path.is_file():
                bundle.write(path, str(path.relative_to(root/"dist")))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (release/"SHA256SUMS").write_text(checksum+"  "+archive.name+"\n", encoding="utf-8")
    return {"executable": str(binary), "archive": str(archive), "sha256": checksum, "mcp_smoke_passed": True}


if __name__ == "__main__":
    main()
