from pathlib import Path
import uuid
from ..storage import save, read, within
from ..serialization import canonical


def validate_session(session):
    if not isinstance(session,dict) or session.get('schema_version')!='planning-session.v1': raise ValueError('Invalid planning session')
    uuid.UUID(session['session_id'])
    for key in ('candidates','rollouts','scores','outcomes'):
        if not isinstance(session.get(key),list): raise ValueError('Invalid planning collection')
        if any(not isinstance(value,dict) for value in session[key]): raise ValueError('Invalid planning record')
    if len(session['candidates'])>8 or len(session['rollouts'])>64 or len(session['scores'])>64 or len(session['outcomes'])>64: raise ValueError('Planning record count exceeded')
    if session.get('status') not in ('planning','selected','failed','execution_denied','execution_error') or not isinstance(session.get('origin'),dict) or not isinstance(session.get('budget'),dict): raise ValueError('Invalid planning status/origin/budget')
    from .contracts import CandidateAction, TrajectoryScore, PredictionDistribution, finite
    for candidate in session['candidates']: CandidateAction(**candidate)
    for score in session['scores']: TrajectoryScore(**score)
    selection=session.get('selection')
    if selection is not None:
        if not isinstance(selection,dict) or not isinstance(selection.get('alternative_candidates'),list): raise ValueError('Invalid selection')
        selected=CandidateAction(**selection['selected_action'])
        if selected.candidate_id!=selection['selected_candidate_id']: raise ValueError('Selection ID mismatch')
        for item in selection['alternative_candidates']:
            if not isinstance(item,dict): raise ValueError('Invalid alternative')
            finite(item['score'],'alternative score',-1e100,1e100);finite(item['risk'],'alternative risk',0,1)
    for outcome in session['outcomes']:
        if type(outcome.get('outcome_match')) is not bool: raise ValueError('Invalid prediction evaluation')
    for rollout in session['rollouts']:
        if not isinstance(rollout.get('simulated_steps'),list) or len(rollout['simulated_steps'])>8: raise ValueError('Invalid imagined steps')
        for step in rollout['simulated_steps']:
            if not isinstance(step,dict) or step.get('simulated') is not True: raise ValueError('Imagined step is not real evidence')
            action=CandidateAction(**step['action'])
            PredictionDistribution.parse(step['prediction'],action.candidate_id)
    if len(canonical(session).encode())>4*1024*1024: raise ValueError('Planning session too large')
    if any(r.get('synthetic') is not True for r in session['rollouts']): raise ValueError('Imagination must be marked simulated')
    return session


class PlanningStore:
    def __init__(self, root): self.root=Path(root).resolve()
    def put(self, session):
        validate_session(session)
        folder=within(self.root,'planning/sessions');folder.mkdir(parents=True,exist_ok=True)
        if len(list(folder.glob('*.json')))>=1000: raise ValueError('Planning session artifact limit')
        path=within(folder,session['session_id']+'.json')
        if path.exists() and canonical(read(path))!=canonical(session): raise ValueError('Planning session ID conflict')
        save(path,session);return path
    def get(self, session_id):
        uuid.UUID(session_id)
        return validate_session(read(within(self.root,'planning/sessions/'+session_id+'.json')))
    def list(self): return [validate_session(read(p)) for p in sorted((self.root/'planning/sessions').glob('*.json'))]
