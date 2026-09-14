"""Minimal portable text/tool IR. Unsupported blocks fail before transport."""
import json

def to_chat(request):
    allowed={'model','messages','tools','generation','tool_choice','boundary_id'}
    if set(request)-allowed:raise ValueError('Unsupported model IR field')
    body={'model':request['model'],'messages':[],**request.get('generation',{})}
    for m in request['messages']:
        role=m['role'];text=[];calls=[]
        for block in m['content']:
            kind=block['type']
            if kind=='text':text.append(block['text'])
            elif kind=='tool_call':calls.append({'id':block['id'],'type':'function','function':{'name':block['name'],'arguments':json.dumps(block['arguments'])}})
            elif kind=='tool_result':
                if len(m['content'])!=1:raise ValueError('Tool results require a separate message')
                body['messages'].append({'role':'tool','tool_call_id':block['call_id'],'content':block['content'] if isinstance(block['content'],str) else json.dumps(block['content'])})
            else:raise ValueError('Unsupported IR block: '+kind)
        if text or calls:
            item={'role':role,'content':'\n'.join(text) or None}
            if calls:item['tool_calls']=calls
            body['messages'].append(item)
    if request.get('tools'):body['tools']=[{'type':'function','function':{'name':t['name'],'description':t.get('description',''),'parameters':t['input_schema']}} for t in request['tools']]
    if 'tool_choice' in request:body['tool_choice']=request['tool_choice']
    return body

def from_chat(payload):
    choice=payload['choices'][0];m=choice['message'];blocks=[]
    if m.get('content'):blocks.append({'type':'text','text':m['content']})
    for c in m.get('tool_calls',[]):blocks.append({'type':'tool_call','id':c['id'],'name':c['function']['name'],'arguments':json.loads(c['function']['arguments'])})
    return {'role':'assistant','content':blocks,'finish_reason':choice.get('finish_reason'),'usage':payload.get('usage',{}),'native_response':payload.get('native_response',payload)}
