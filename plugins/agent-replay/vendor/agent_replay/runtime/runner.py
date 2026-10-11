import asyncio
import inspect
from pathlib import Path
from ..capture import Capture
from ..migrations import legacy,to_v2
from ..project import callable_for
from ..serialization import digest
from ..storage import save
from .policy import Policy
from .fidelity import doctor

async def async_run(trace,spec,project,directory):
    from ..security.replay import deny_execution
    deny_execution()

def run(trace,spec,project,directory): return asyncio.run(async_run(trace,spec,project,directory))
