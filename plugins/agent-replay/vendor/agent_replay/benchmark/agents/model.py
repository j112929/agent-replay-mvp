"""Optional bounded Chat Completions tool loop using the existing provider transport."""
import json
from ..errors import ToolLoop


class OpenAICompatibleAgent:
    def __init__(self, config):
        if not config.model:
            raise ValueError('--model is required for --agent openai')
        self.config = config

    async def run(self, task, environment, capture):
        definitions = {
            'execute': {'command': {'type': 'string'}},
            'read_file': {'path': {'type': 'string'}},
            'write_file': {'path': {'type': 'string'}, 'content': {'type': 'string'}},
            'list_files': {'path': {'type': 'string'}}}
        tools = [{'type': 'function', 'function': {'name': name,
            'description': 'Local working-directory ' + name,
            'parameters': {'type': 'object', 'properties': properties,
                           'required': list(properties), 'additionalProperties': False}}}
                 for name, properties in definitions.items()]
        messages = [{'role': 'system', 'content': self.config.parameters.get('system_prompt',
            'Complete the task using tools. Shell cwd is the workspace; use relative shell paths. File tools accept /workspace as a virtual prefix. Finish with a short answer.')},
            {'role': 'user', 'content': task.instruction}]
        for _ in range(self.config.parameters.get('max_model_calls', 20)):
            response = capture.chat(model=self.config.model, messages=messages, tools=tools,
                                   base_url=self.config.parameters.get('base_url'))
            message = response['choices'][0]['message']
            messages.append(message)
            calls = message.get('tool_calls') or []
            if not calls:
                return message.get('content')
            for call in calls:
                name = call['function']['name']
                if name not in definitions:
                    raise ValueError('Model requested unknown tool: ' + name)
                value = getattr(environment, name)(**json.loads(call['function']['arguments']))
                messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(value)})
        raise ToolLoop('Model call budget exhausted')
