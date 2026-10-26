"""Windows launch gate: no provider code runs before Job Object assignment."""
import subprocess
import sys


def main():
    if sys.stdin.buffer.read(1) != b"\x01":
        return 125
    payload = sys.stdin.buffer.read()
    # Child inherits its host's Windows Job Object and original stdout/stderr.
    result = subprocess.run(sys.argv[1:], input=payload, shell=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
