"""Key-free regression story, using observable payment test doubles."""
import hashlib
from pathlib import Path
from .capture import capture,tool
from .storage import save,read
from .regression.runner import run_suite
from .project import load_project

@tool(name='orders.lookup',boundary_id='orders.lookup',side_effect='read')
def lookup(order_id):return {'order_id':order_id,'approved_minor':3000,'paid_minor':29900}

@tool(name='payments.refund',boundary_id='payments.refund',side_effect='write')
def refund(order_id,amount_minor):return {'ok':True,'simulated':True,'amount_minor':amount_minor}

def broken(run):
    order=lookup(run.input['order_id'])
    return refund(order['order_id'],order['paid_minor'])

def fixed(run):
    order=lookup(run.input['order_id'])
    return refund(order['order_id'],min(2900,order['approved_minor']))

def skipped(run):return {'ok':True}

def demo(directory):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    with capture('Refund limit',directory/'traces',input={'order_id':'o_1042'},durability='sync') as run:
        value=broken(run);run.set_output(value);run.fail('Refund exceeds approval',category='assertion')
    fixture={'schema_version':'1.0','fixtures':[{'id':'refund-spy','selector':{'boundary_id':'payments.refund'},'consume':'repeat','expected_calls':1,'response':{'kind':'return','value':{'ok':True,'simulated':True}}}]}
    save(directory/'fixtures.json',fixture)
    project={'schema_version':'1.0','project_id':'demo','entrypoints':{'broken':{'callable':'agent_replay.demo:broken'},'fixed':{'callable':'agent_replay.demo:fixed'},'skipped':{'callable':'agent_replay.demo:skipped'}},'suites':{'refund':['refund.case.json']},'error_mappings':{'ValueError':'ValueError'}}
    save(directory/'agent-replay.json',project)
    case={'schema_version':'1.0','id':'refund-limit','title':'Refund cannot exceed approved amount','tags':['offline'],'source':{'trace_path':str(Path(run.path).relative_to(directory)),'sha256':hashlib.sha256(Path(run.path).read_bytes()).hexdigest()},'entrypoint_id':'broken',
          'replay_spec':{'scope':'agent','policy':'frozen','fixture_set':'fixtures.json','limits':{'max_model_calls':0,'timeout_seconds':20}},
          'assertions':[{'id':'completed','op':'execution_completed'},{'id':'one-refund','select':{'boundary_id':'payments.refund'},'op':'count','expected':1},{'id':'amount-limit','select':{'boundary_id':'payments.refund'},'path':'/input/value/amount_minor','op':'lte','expected':3000},{'id':'correct-order','select':{'boundary_id':'payments.refund'},'path':'/input/value/order_id','op':'equals','expected':'o_1042'}]}
    path=directory/'refund.case.json';save(path,case);configured=load_project(directory/'agent-replay.json')
    before=run_suite([path],configured,directory/'before')
    case['entrypoint_id']='fixed';save(path,case);after=run_suite([path],configured,directory/'after')
    case['entrypoint_id']='broken';save(path,case);regressed=run_suite([path],configured,directory/'regressed')
    case['entrypoint_id']='fixed';save(path,case)
    if (before['exit_code'],after['exit_code'],regressed['exit_code'])!=(1,0,1):raise ValueError('Demo did not produce fail/pass/fail')
    return {'synthetic':True,'external_calls':0,'before':before,'after':after,'regressed':regressed,'case':str(path),'project':str(directory/'agent-replay.json')}
