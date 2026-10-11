"""Normalized execution exceptions usable by any adapter or agent."""
class ToolError(RuntimeError):
    def __init__(self, message, metadata=None):
        super().__init__(message)
        self.metadata = metadata or {}

class PolicyViolation(ValueError):
    pass

class ToolLoop(RuntimeError):
    pass
