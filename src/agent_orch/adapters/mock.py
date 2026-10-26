import asyncio

from .base import ProviderBlocked


class MockAdapter:
    async def execute(self, task, workspace, attempt, cancelled):
        behavior = task.get("mock_behavior", "good")
        if behavior in ("quota", "permission"):
            raise ProviderBlocked(behavior)
        if behavior == "timeout":
            while True:
                if cancelled():
                    raise asyncio.CancelledError()
                await asyncio.sleep(0.02)
        bad = behavior == "bad" or (behavior == "bad-first" and attempt == 1)
        return {"summary": "Mock claims success; verifier must check it", "files": [
            {"path": "stats.py", "content": "def total(values):\n    return sum(values) if values else " + ("999" if bad else "0") + "\n"}]}
