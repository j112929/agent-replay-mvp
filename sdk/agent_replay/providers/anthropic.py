"""Non-streaming text/tool Chat IR bridge to Anthropic Messages."""
import json
from urllib.request import Request,build_opener
from urllib.parse import urlparse
from ..provider import NoRedirect,ProviderError
from ..schema import sanitize

def normalize(request):
    allowed={'model','messages','tools','tool_choice','max_tokens','max_completion_tokens','temperature','top_p','stop','stream'}
    if set(request)-allowed or request.get('stream'): raise ValueError('Unsupported Anthropic request parameters')
    system=[]; messages=[]
    for m in request['messages']:
        if m['role']=='system':
            if not isinstance(m.get('content'),str): raise ValueError('Only text system messages are supported')
            system.append(m['content']);continue
        if m['role']=='tool':
            messages.append({'role':'user','content':[{'type':'tool_result','tool_use_id':m['tool_call_id'],'content':m['content']}]});continue
        if m['role'] not in ('user','assistant') or (m.get('content') is not None and not isinstance(m['content'],str)): raise ValueError('Only text and tool messages are supported')
        blocks=[]
        if m.get('content'): blocks.append({'type':'text','text':m['content']})
        for call in m.get('tool_calls',[]):
            blocks.append({'type':'tool_use','id':call['id'],'name':call['function']['name'],'input':json.loads(call['function']['arguments'])})
        messages.append({'role':m['role'],'content':blocks})
    body={'model':request['model'],'messages':messages,'max_tokens':request.get('max_tokens',request.get('max_completion_tokens',1024))}
    if system: body['system']='\n\n'.join(system)
    for k in ('temperature','top_p'):
        if k in request: body[k]=request[k]
    if 'stop' in request: body['stop_sequences']=[request['stop']] if isinstance(request['stop'],str) else request['stop']
    if request.get('tools'):
        body['tools']=[{'name':t['function']['name'],'description':t['function'].get('description',''),'input_schema':t['function']['parameters']} for t in request['tools']]
    if 'tool_choice' in request:
        choice=request['tool_choice']
        if choice in ('auto','required'): body['tool_choice']={'type':'auto' if choice=='auto' else 'any'}
        elif isinstance(choice,dict): body['tool_choice']={'type':'tool','name':choice['function']['name']}
        else: raise ValueError('Unsupported tool_choice')
    return body

def completion(request,key,base_url):
    parsed=urlparse(base_url)
    if parsed.scheme!='https' and not(parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1')): raise ValueError('Invalid provider endpoint')
    if parsed.username or parsed.password or parsed.query or parsed.fragment: raise ValueError('Invalid endpoint components')
    if not key and parsed.hostname not in ('localhost','127.0.0.1'): raise ValueError('Provider key is not configured')
    body=normalize(request)
    req=Request(base_url.rstrip('/')+'/v1/messages',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','x-api-key':key,'anthropic-version':'2023-06-01'},method='POST')
    try:
        with build_opener(NoRedirect).open(req,timeout=60) as r: raw=r.read(5*1024*1024+1)
        if len(raw)>5*1024*1024: raise ProviderError('Response exceeds limit')
        payload=json.loads(raw)
    except Exception as exc:
        raise ProviderError(sanitize(str(exc).replace(key,'[REDACTED]') if key else str(exc))) from None
    message={'role':'assistant','content':''}; calls=[]
    for b in payload['content']:
        if b['type']=='text': message['content']+=b['text']
        elif b['type']=='tool_use': calls.append({'id':b['id'],'type':'function','function':{'name':b['name'],'arguments':json.dumps(b['input'])}})
        else: raise ProviderError('Unsupported response block: '+b['type'])
    if calls: message['tool_calls']=calls
    usage=payload.get('usage',{})
    return {'choices':[{'message':message,'finish_reason':payload.get('stop_reason')}],'usage':{'input_tokens':usage.get('input_tokens',0),'output_tokens':usage.get('output_tokens',0),'total_tokens':usage.get('input_tokens',0)+usage.get('output_tokens',0)},'native_response':payload}
