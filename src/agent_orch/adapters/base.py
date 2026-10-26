from typing import Protocol


class WorkerAdapter(Protocol):
    async def execute(self, task: dict, workspace, attempt: int, cancelled) -> dict: ...


class ProviderBlocked(RuntimeError):
    pass
