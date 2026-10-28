"""Explicit local cost reservations. CLI estimates are not invoice hard caps."""
from __future__ import annotations

import math
import sqlite3
import time
import uuid
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .adapters.base import ProviderBlocked
from .models import OrchError


def cents(value):
    try:
        amount = Decimal(str(value)) * 100
        if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value():
            raise ValueError()
        return int(amount)
    except (InvalidOperation, ValueError, TypeError) as e:
        raise OrchError("Budget must be a positive finite USD amount in whole cents") from e


def external(path, workspace=None):
    path = Path(path).resolve()
    if path.is_relative_to(Path(__file__).resolve().parents[2]) or (workspace and path.is_relative_to(Path(workspace).resolve())):
        raise OrchError("Budget ledger must be outside source/workspace and repository")
    return path


def create_budget(path, total_usd, per_call_usd):
    """Trusted local authorization entry, never callable by a worker/model."""
    path = external(path)
    total, per_call = cents(total_usd), cents(per_call_usd)
    if per_call > total:
        raise OrchError("Per-call reservation exceeds total authorization")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb"):
        pass
    with closing(sqlite3.connect(path)) as db:
        with db:
            db.executescript("CREATE TABLE authorization(version INTEGER, total INTEGER, per_call INTEGER, halted INTEGER);"
                             "CREATE TABLE calls(id TEXT PRIMARY KEY, purpose TEXT, reserved INTEGER, state TEXT, reported_cost REAL, created REAL);")
            db.execute("INSERT INTO authorization VALUES(1,?,?,0)", (total, per_call))
    return {"ledger": str(path), "total_usd": total/100, "per_call_usd": per_call/100, "billing_verified": False}


class Budget:
    def __init__(self, path, workspace=None):
        if not path:
            raise ProviderBlocked("Claude requires an explicitly authorized budget ledger; no paid fallback")
        self.path = external(path, workspace)
        if not self.path.is_file() or Path(path).is_symlink():
            raise ProviderBlocked("Existing authorized budget ledger required")
        self.db = sqlite3.connect(self.path.as_uri()+"?mode=rw", uri=True, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        try:
            row = self.db.execute("SELECT * FROM authorization").fetchall()
            if len(row) != 1 or row[0]["version"] != 1 or not 0 < row[0]["per_call"] <= row[0]["total"]:
                raise OrchError("Unsupported budget authorization")
            self.per_call = row[0]["per_call"]/100
        except sqlite3.Error as e:
            self.db.close()
            raise OrchError("Invalid budget ledger schema") from e
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def reserve(self, purpose):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            auth = self.db.execute("SELECT * FROM authorization").fetchone()
            used = self.db.execute("SELECT COALESCE(SUM(reserved),0) FROM calls").fetchone()[0]
            if auth["halted"] or used+auth["per_call"] > auth["total"]:
                raise ProviderBlocked("Authorized Claude reservations exhausted or halted; no inference launched")
            token = uuid.uuid4().hex
            self.db.execute("INSERT INTO calls VALUES(?,?,?,'reserved',NULL,?)", (token, purpose[:200], auth["per_call"], time.time()))
            self.db.execute("COMMIT")
            return token
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def finish(self, token, *, state, cost=None):
        valid_cost = type(cost) in (int, float) and math.isfinite(cost) and cost >= 0
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT * FROM calls WHERE id=?", (token,)).fetchone()
            if not row or row["state"] != "reserved":
                raise OrchError("Unknown or already settled reservation")
            overrun = valid_cost and Decimal(str(cost))*100 > row["reserved"]
            if state == "unknown":
                self.db.execute("UPDATE authorization SET halted=1")
            if overrun:
                state = "reported_overrun"
                self.db.execute("UPDATE authorization SET halted=1")
            self.db.execute("UPDATE calls SET state=?,reported_cost=? WHERE id=?", (state, cost if valid_cost else None, token))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        if overrun:
            raise ProviderBlocked("CLI estimate exceeded reservation; budget halted for reconciliation")

    def report(self):
        auth = self.db.execute("SELECT * FROM authorization").fetchone()
        calls = [dict(row) for row in self.db.execute("SELECT id,purpose,reserved,state,reported_cost FROM calls ORDER BY created")]
        used = sum(c["reserved"] for c in calls)
        known = [c["reported_cost"] for c in calls if c["reported_cost"] is not None]
        return {"total_usd": auth["total"]/100, "per_call_usd": auth["per_call"]/100,
                "reserved_usd": used/100, "remaining_reservation_usd": (auth["total"]-used)/100,
                "reported_cost_usd": sum(known) if known else None, "unknown_cost_calls": len(calls)-len(known), "halted": bool(auth["halted"]),
                "calls": calls, "billing_verified": False}
