import os
from ..provider import completion

def complete(project,provider_id,request):
    config=project.get('providers',{}).get(provider_id)
    if not config: raise ValueError('Unknown provider ID')
    kind=config.get('kind','openai_compatible')
    key=os.environ.get(config.get('api_key_env','OPENAI_API_KEY'),'')
    if kind=='openai_compatible': return completion(request,base_url=config.get('base_url'),api_key=key)
    if kind=='anthropic':
        from .anthropic import completion as anthropic
        return anthropic(request,key,config.get('base_url','https://api.anthropic.com'))
    raise ValueError('Unsupported provider kind')
