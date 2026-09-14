from ..migrations import to_v2

def doctor(trace,project,entrypoint):
    t=to_v2(trace); gaps=[]
    if entrypoint not in project.get('entrypoints',{}): gaps.append('Entrypoint is not registered')
    if not t['input'].get('present') or t['input'].get('replayability')!='complete': gaps.append('Run input unavailable or redacted')
    if t['capture_health'].get('state')!='complete': gaps.append('Capture is incomplete')
    if t['execution']['status'] in ('running','interrupted'): gaps.append('Source run did not finish')
    return {'ready':not gaps,'gaps':gaps,'scope':'agent','level':'controlled_boundaries','limitations':['Uninstrumented code still executes normally','From-start rerun, not checkpoint resume','Dependency identifiers do not reconstruct an environment']}
