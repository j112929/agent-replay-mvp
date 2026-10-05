"""Synthetic scripted agents, deliberately not a general benchmark solver."""
import asyncio


class DemoAgent:
    def __init__(self, variant='demo'):
        self.variant = variant

    async def run(self, task, environment, capture):
        actions = task.metadata.get('demo_actions', {}).get(self.variant)
        if actions is None:
            raise ValueError(f'No synthetic {self.variant} script for {task.id}')
        for action in actions:
            operation = action['operation']
            if operation == 'wait':
                with capture.span('wait', input={'seconds': action['seconds']}):
                    await asyncio.sleep(action['seconds'])
            elif operation in ('write_file', 'read_file', 'list_files', 'execute'):
                getattr(environment, operation)(**action.get('arguments', {}))
            else:
                raise ValueError('Unknown demo operation: ' + operation)
        return {'synthetic': True, 'variant': self.variant}
