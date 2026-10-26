import json

from .base import ProviderBlocked


class ManualAdapter:
    def __init__(self, response=None):
        self.response = response

    async def execute(self, task, workspace, attempt, cancelled):
        if self.response is None:
            raise ProviderBlocked("manual: supply --manual-result JSON file; worker completion requires independent verification")
        return json.loads(self.response.read_text(encoding="utf-8"))
