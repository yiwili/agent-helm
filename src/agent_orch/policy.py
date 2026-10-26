"""Pure, fail-closed routing policy. No hidden paid fallback."""
from dataclasses import dataclass

from .models import OrchError


@dataclass(frozen=True)
class Route:
    provider: str
    reason: str


def select_provider(reports, *, allow_manual=True, api_budget=0, allow_gui=False):
    if api_budget != 0 or allow_gui:
        raise OrchError("Paid API and GUI routing are unimplemented and disabled")
    for report in reports:
        if all(report.get(x) is True for x in ("enabled", "authenticated", "smoke_passed", "billing_verified")) and report.get("billing_source") == "subscription" and not report.get("cooldown"):
            return Route(report["provider"], "verified subscription route")
    if allow_manual:
        return Route("manual", "no verified unattended subscription route")
    return Route("blocked", "no authorized route; no API fallback")
