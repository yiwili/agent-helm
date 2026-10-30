#!/usr/bin/env python3
"""CLI utility to expand AHCP-1 compact wire outputs into canonical Agent Helm JSON templates & Chinese reports.

Usage examples:
  python scripts/ahcp_codec.py expand-result '{"s":"Fixed empty sum","f":[{"p":"stats.py","c":"def total(values):\\n    return sum(values) if values else 0\\n"}]}'
  python scripts/ahcp_codec.py expand-review '{"d":"A","h":"sha256...","r":["All checks pass"],"e":["D","V"]}'
  python scripts/ahcp_codec.py parse-wire "AHCP1|REV|A|abc1234|D,V,B,S|All unit tests pass"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_orch.models import normalize_worker_result
from agent_orch.wire import (
    expand_wire_object,
    expand_wire_result,
    expand_wire_review,
    parse_ahcp_text,
    translate_summary_to_zh,
)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="AHCP-1 token-saving wire codec and template expander")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_res = sub.add_parser("expand-result", help="Expand compact {'s','f'} or AHCP1|RES wire text into worker-result.v2 template")
    p_res.add_argument("payload", help="Compact JSON or AHCP-1 wire string (or '-' for stdin)")
    p_res.add_argument("--provider", default="qoder-cn")
    p_res.add_argument("--role", default="worker")
    p_res.add_argument("--allowed-path", action="append", dest="allowed", default=["stats.py"])

    p_rev = sub.add_parser("expand-review", help="Expand compact {'d','h','r','e'} or AHCP1|REV wire text into canonical review JSON")
    p_rev.add_argument("payload", help="Compact JSON or AHCP-1 wire string (or '-' for stdin)")

    p_wire = sub.add_parser("parse-wire", help="Auto-detect and expand any AHCP-1 wire payload")
    p_wire.add_argument("payload", help="Compact JSON or AHCP-1 wire string (or '-' for stdin)")

    args = parser.parse_args(argv)
    raw = sys.stdin.read() if args.payload == "-" else args.payload

    if args.cmd == "expand-result":
        base = parse_ahcp_text(raw) if not raw.lstrip().startswith("{") else expand_wire_result(json.loads(raw))
        envelope = normalize_worker_result(base, args.allowed, provider=args.provider, attempt=1)
        envelope["extensions"]["wire_protocol"] = "AHCP-1"
        envelope["extensions"]["wire_language"] = "en"
        envelope["extensions"]["role"] = args.role
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "expand-review":
        review = parse_ahcp_text(raw) if not raw.lstrip().startswith("{") else expand_wire_review(json.loads(raw))
        print(json.dumps(review, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "parse-wire":
        expanded = parse_ahcp_text(raw) if not raw.lstrip().startswith("{") else expand_wire_object(json.loads(raw))
        print(json.dumps(expanded, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
