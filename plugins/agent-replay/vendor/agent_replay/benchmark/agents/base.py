from typing import Any, Protocol
from ..contracts import BenchmarkTask


class BenchmarkAgent(Protocol):
    async def run(self, task: BenchmarkTask, environment: Any, capture: Any) -> Any: ...
