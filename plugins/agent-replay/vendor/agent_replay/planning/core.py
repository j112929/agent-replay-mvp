"""Receding-horizon planning over copied JSON; authority lives in the executor."""
import asyncio
import copy
from dataclasses import asdict
import random
from statistics import mean, pvariance
import math
import time
from .contracts import (PlanningConfig, AgentContext, CandidateAction, PredictionConfig,
    PredictionDistribution, ImaginedTrajectory, TrajectoryScore, ident, data)
from ..regression.predicates import validate_schema, schema_matches
from ..serialization import digest, canonical


class PlanningFailure(ValueError): pass


def sample_prediction(prediction, rng):
    """Data-only draw shared by online imagination and bounded research sampling."""
    probabilities = [o.get('probability') for o in prediction.outcomes]
    return copy.deepcopy(rng.choices(prediction.outcomes,
        weights=probabilities if all(p is not None for p in probabilities) else None,k=1)[0])


class CandidateGenerator:
    def __init__(self, policy, schemas):
        self.policy, self.schemas = policy, copy.deepcopy(schemas)
        for schema in self.schemas.values(): validate_schema(schema)

    def validate(self, action):
        if not isinstance(action,CandidateAction) or action.tool_name not in self.schemas or not schema_matches(action.action_payload,self.schemas[action.tool_name]):
            raise PlanningFailure('Candidate does not match an authorized tool schema')
        if '[REDACTED]' in canonical(action.action_payload): raise PlanningFailure('Unavailable sensitive action payload')
        return action

    async def generate(self, context, k):
        values = self.policy(copy.deepcopy(context))
        if not isinstance(values,list) or len(values)>64: raise PlanningFailure('Invalid candidate policy output')
        result=[]; seen=set()
        for item in values:
            action=self.validate(item)
            if action.key not in seen:
                seen.add(action.key); result.append(action)
            if len(result)==k: break
        if not result: raise PlanningFailure('No valid candidates')
        if len({a.candidate_id for a in result}) != len(result): raise PlanningFailure('Duplicate candidate IDs')
        return result


class TrajectoryScorer:
    def __init__(self, config): self.config=config

    async def score(self, trajectory, goal):
        if not trajectory.simulated_steps: raise PlanningFailure('Empty imagination')
        last=trajectory.simulated_steps[-1]['sampled_outcome']
        probability=last.get('success_probability')
        if any(any(o.get('probability') is None for o in s['prediction']['outcomes']) for s in trajectory.simulated_steps): probability=None
        reward=last.get('reward',0)
        risk=1-probability if probability is not None else 1
        cost=sum(s['sampled_outcome'].get('cost_usd',0) for s in trajectory.simulated_steps)
        value=self.config.value_weight*reward-self.config.lambda_cost*cost-self.config.lambda_risk*risk-self.config.lambda_uncertainty*trajectory.uncertainty
        return TrajectoryScore(trajectory.imagination_id,trajectory.candidate_id,reward,cost,risk,
            trajectory.uncertainty,len(trajectory.simulated_steps),probability,value)


class ActionSelector:
    async def select(self,candidates,rollouts,scores):
        alternatives=[]
        for action in candidates:
            matched=[s for s in scores if s['candidate_id']==action.candidate_id]
            if not matched: raise PlanningFailure('Incomplete candidate evidence')
            probabilities=[s['estimated_success_probability'] for s in matched]
            roots=[r['simulated_steps'][0]['prediction']['outcomes'] for r in rollouts if r['candidate_id']==action.candidate_id]
            entropies=[-sum(o['probability']*math.log2(o['probability']) for o in root if o['probability']>0) for root in roots if all(o.get('probability') is not None for o in root)]
            alternatives.append({'candidate_id':action.candidate_id,'score':mean(s['score'] for s in matched),
                'outcome_entropy_bits':mean(entropies) if len(entropies)==len(roots) and roots else None,
                'score_variance':pvariance(s['score'] for s in matched),'uncertainty':mean(s['uncertainty'] for s in matched),
                'expected_reward':mean(s['expected_task_reward'] for s in matched),'expected_cost':mean(s['expected_execution_cost'] for s in matched),
                'risk':mean(s['predicted_failure_risk'] for s in matched),
                'success_probability':mean(probabilities) if all(p is not None for p in probabilities) else None})
        chosen=max(alternatives,key=lambda a:a['score'])
        selected=next(a for a in candidates if a.candidate_id==chosen['candidate_id'])
        return {'selected_candidate_id':selected.candidate_id,'selected_action':asdict(selected),
            'selection_score':chosen['score'],'selection_reason':'Maximum mean reward minus configured cost, failure loss and uncertainty',
            'alternative_candidates':alternatives,'predicted_risks':chosen['risk'],
            'success_probability':chosen['success_probability'],'calibration_status':'uncalibrated'}


class Planner:
    version='mpc.v1'
    def __init__(self, generator, world_model, config=None, scorer=None, selector=None):
        self.generator,self.model=generator,world_model
        self.config=config or PlanningConfig()
        self.scorer=scorer or TrajectoryScorer(self.config)
        self.selector=selector or ActionSelector()

    async def rollout(self,context,initial_action,horizon=None,num_samples=None):
        """Public imagination interface; same bounded engine, pinned first action."""
        parent=self.generator
        class PinnedGenerator:
            async def generate(self,branch,k):
                return [copy.deepcopy(parent.validate(initial_action))] if branch.state_digest==context.state_digest else await parent.generate(branch,k)
        config=PlanningConfig(**{**asdict(self.config),'candidates':1,
            'horizon':horizon or self.config.horizon,'rollouts_per_candidate':num_samples or self.config.rollouts_per_candidate})
        result=await Planner(PinnedGenerator(),self.model,config,self.scorer,self.selector).plan(context)
        if result['status']!='selected': raise PlanningFailure('Imagination unavailable')
        return [ImaginedTrajectory(**r) for r in result['rollouts']]

    async def plan(self, context):
        if not self.config.enabled: raise PlanningFailure('Planning is disabled')
        start=time.monotonic(); deadline=start+self.config.max_planning_latency_ms/1000
        counts={'predictions':0,'model_calls':0,'tokens':0,'reserved_tokens':0,'cost_usd':0,'usage_complete':True}
        def generation_cost(actions):
            if hasattr(self.generator,'last_generation_cost'):
                value=self.generator.last_generation_cost
                if value is None:counts['cost_usd']=None
                elif counts['cost_usd'] is not None:counts['cost_usd']+=value
                return
            for action in actions:
                if action.generation_cost is None: counts['cost_usd']=None
                elif counts['cost_usd'] is not None: counts['cost_usd']+=action.generation_cost
        session={'schema_version':'planning-session.v1','session_id':ident(),'planner_version':self.version,
            'created_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
            'benchmark_run_id':context.benchmark_run_id,'trajectory_id':context.trajectory_id,
            'task_id':context.task_id,'task_group':context.task_group,'origin':asdict(context),
            'origin_state_digest':context.state_digest,'planning_config':asdict(self.config),
            'world_model':{'id':getattr(self.model,'model_id','custom'),'version':getattr(self.model,'version','unspecified')},
            'candidates':[],'rollouts':[],'scores':[],'outcomes':[],'status':'planning','budget':counts,
            'execution_kind':'HYPOTHETICAL_ONLY','real_execution':None}
        async def bounded(awaitable):
            remaining=deadline-time.monotonic()
            if remaining<=0:
                if hasattr(awaitable,'close'): awaitable.close()
                raise PlanningFailure('planning_timeout')
            value=await asyncio.wait_for(awaitable,remaining)
            if time.monotonic()>deadline: raise PlanningFailure('planning_timeout')
            return value
        try:
            candidates=await bounded(self.generator.generate(context,self.config.candidates))
            generation_cost(candidates)
            session['candidates']=[asdict(a) for a in candidates]
            # Each candidate gets its own deterministic stream, not shared hypothetical state.
            for index, action in enumerate(candidates):
                for sample in range(self.config.rollouts_per_candidate):
                    rng=random.Random(self.config.seed+index*10007+sample)
                    branch=copy.deepcopy(context); current=copy.deepcopy(action); steps=[]; uncertainty=0; reason='horizon'
                    for depth in range(self.config.horizon):
                        if counts['predictions']>=self.config.max_predictions: reason='prediction_budget'; break
                        reserve=getattr(self.model,'token_reservation',lambda c,a:0)(branch,current)
                        if counts['tokens']+counts['reserved_tokens']+reserve>self.config.max_planning_tokens: reason='token_budget'; break
                        counts['predictions']+=1;counts['reserved_tokens']+=reserve
                        prediction=await bounded(self.model.predict(copy.deepcopy(branch),copy.deepcopy(current),
                            PredictionConfig(self.config.seed+sample+depth,512,max(1,(deadline-time.monotonic())*1000))))
                        prediction=PredictionDistribution.parse(asdict(prediction),current.candidate_id)
                        calls=prediction.metadata.get('model_calls',0)
                        if type(calls) is not int or calls<0: raise PlanningFailure('Invalid prediction usage')
                        counts['model_calls']+=calls
                        usage=prediction.prediction_token_usage
                        if usage is None:
                            counts['usage_complete']=False
                        else:
                            counts['reserved_tokens']-=reserve;counts['tokens']+=usage
                        if prediction.cost_usd is None: counts['cost_usd']=None
                        elif counts['cost_usd'] is not None: counts['cost_usd']+=prediction.cost_usd
                        if counts['tokens']+counts['reserved_tokens']>self.config.max_planning_tokens: raise PlanningFailure('Model exceeded token budget')
                        probabilities=[o.get('probability') for o in prediction.outcomes]
                        chosen=sample_prediction(prediction,rng)
                        # Unknown weights are samples for exploration, never a calibrated distribution.
                        uncertainty=1-(1-uncertainty)*(1-chosen.get('uncertainty',1))
                        steps.append({'simulation_depth':depth,'action':asdict(current),'context':asdict(branch),
                            'prediction':asdict(prediction),'sampled_outcome':copy.deepcopy(chosen),'simulated':True})
                        branch=branch.branch(chosen['state_delta'],chosen['observation'],current)
                        if chosen['terminal']: reason='terminal'; break
                        if depth+1<self.config.horizon:
                            current=(await bounded(self.generator.generate(branch,1)))[0]
                            generation_cost([current])
                    if not steps: raise PlanningFailure(reason)
                    imagined=ImaginedTrajectory(ident(),action.candidate_id,context.state_digest,steps,branch.state,uncertainty,reason)
                    score=await bounded(self.scorer.score(imagined,context.goal))
                    session['rollouts'].append(asdict(imagined));session['scores'].append(asdict(score))
                    if len(canonical(session).encode())>128*1024: raise PlanningFailure('Planning record size budget')
            session['selection']=await bounded(self.selector.select(candidates,session['rollouts'],session['scores']))
            session['selection']['planning_metadata']={'origin_state_digest':context.state_digest,'world_model':session['world_model']}
            selected=CandidateAction(**session['selection']['selected_action'])
            if session['selection']['selected_candidate_id']!=selected.candidate_id or not any(a.candidate_id==selected.candidate_id and a.key==selected.key for a in candidates): raise PlanningFailure('Selector returned an unavailable action')
            session['status']='selected'
        except (Exception, asyncio.TimeoutError) as exc:
            # External/model errors never interpolate prompts, observations or credentials.
            counts['known_cost_lower_bound_usd']=counts.get('cost_usd')
            counts['cost_usd']=None;counts['usage_complete']=False
            session.update(status='failed',reason=type(exc).__name__ if not isinstance(exc,PlanningFailure) else str(exc))
            if counts['reserved_tokens']: counts['usage_complete']=False
            if len(canonical(session).encode())>128*1024:
                session['rollouts']=[];session['scores']=[];session['reason']='record_budget; rollouts unavailable'
        session['planning_latency_ms']=(time.monotonic()-start)*1000
        return session


class PlanningAgent:
    """BenchmarkAgent wrapper, opt-in and restricted to explicit execution adapters."""
    def __init__(self, baseline, planner): self.baseline,self.planner=baseline,planner

    async def run(self, task, environment, capture):
        if not self.planner.config.enabled or self.planner.config.mode=='disabled': return await self.baseline.run(task,environment,capture)
        from .evaluation import evaluate_prediction
        sessions=[];replans=0;irreversible=set();settings=self.planner.config
        capture.trace['metadata']['planning']={'schema_version':'planning-records.v1','sessions':sessions,'mode':settings.mode,
            'limitations':['Hypotheses are not observations; probabilities are uncalibrated until held-out validation']}
        for _ in range(self.planner.config.max_real_steps):
            snapshot=environment.snapshot()
            if snapshot['state'].get('terminal'): break
            benchmark=capture.trace['metadata'].get('benchmark',{})
            context=AgentContext(task.id,task.metadata.get('task_group',task.id),task.instruction,
                snapshot['state'],snapshot['version'],snapshot['environment_version'],
                snapshot.get('history',[]),capture.trace['id'],benchmark.get('run_id'))
            session=await self.planner.plan(context)
            if len(canonical(sessions+[session]).encode())>512*1024:
                capture.trace['metadata']['planning']['termination_reason']='trajectory_planning_record_budget'
                break
            sessions.append(session)
            if settings.mode=='shadow':
                if not hasattr(self.baseline,'choose_action'): session.update(reason='Shadow requires an explicit baseline action');break
                action=self.baseline.choose_action(context)
                session['execution_mode']='shadow';session['executed_baseline_action']=asdict(action)
            elif session['status']=='selected':
                if max((s['uncertainty'] for s in session['scores']),default=1)>settings.max_uncertainty:
                    session.update(status='failed',reason='Uncertainty ceiling exceeded');break
                action=CandidateAction(**session['selection']['selected_action'])
            elif self.planner.config.fallback=='baseline':
                if not hasattr(self.baseline,'choose_action'): session.update(fallback='abort',reason='No explicit baseline action policy');break
                action=self.baseline.choose_action(context)
                session['fallback']='baseline'
            else:
                session['fallback']='abort';break
            # Model-generated permissions and state deltas never reach this boundary.
            self.planner.generator.validate(action)
            effect_key=getattr(environment,'irreversible_key',lambda a:a.key)(action)
            if effect_key in irreversible or not environment.authorize(action,context.state_version):
                session.update(status='execution_denied',reason='State changed or execution policy denied the action');break
            if getattr(environment,'is_irreversible',lambda a:False)(action): irreversible.add(effect_key)
            with capture.span(action.tool_name,kind='tool',input={'action':asdict(action),'state':context.state,
                    'environment_version':context.environment_version,'task_group':context.task_group},
                    planning={'session_id':session['session_id'],'candidate_id':action.candidate_id,'origin_state_digest':context.state_digest}) as span:
                try: actual=environment.execute_action(action)
                except Exception:
                    session.update(status='execution_error',reason='Authorized tool failed; inspect the real tool step')
                    raise
                span.set_output(actual)
                session['real_execution']={'step_id':span.event['id'],'candidate_id':action.candidate_id,
                    'actual_observation':data(actual),'actual_state_version':environment.snapshot()['version']}
            if session['status']=='selected':
                for rollout in session['rollouts']:
                    if rollout['candidate_id']==action.candidate_id:
                        # Root predictions only; future hypothetical observations are not labels.
                        prediction=PredictionDistribution(**rollout['simulated_steps'][0]['prediction'])
                        session['outcomes'].append(evaluate_prediction(prediction,actual,span.event['id']))
                        break
            mismatch=any(not o['outcome_match'] for o in session['outcomes'])
            if settings.stop_on_policy_violation and actual.get('error_category')=='policy_violation':
                session['correction']={'decision':'stop','attempt':replans,'reason':'execution_policy_violation'}
                break
            if actual.get('status')=='error' and not settings.self_correction:
                session['correction']={'decision':'stop','attempt':replans,'reason':'correction_disabled'}
                break
            if settings.self_correction and mismatch:
                terminal=environment.snapshot()['state'].get('terminal',False)
                decision='accept_terminal' if terminal else 'stop' if replans>=settings.max_replans or settings.stop_on_policy_violation and actual.get('error_category')=='policy_violation' else 'replan'
                session['correction']={'decision':decision,'attempt':replans,'reason':'prediction_mismatch','observation_ref':span.event['id']}
                if decision=='stop':break
                if decision=='replan':replans+=1
            else:session['correction']={'decision':'accept','attempt':replans,'reason':'no_enabled_correction_trigger'}
        final=environment.snapshot()['state']
        success=final.get('success') if final.get('terminal') else False
        for session in sessions:
            session['task_outcome']={'success':success,'observed':bool(final.get('terminal'))}
        capture.trace['metadata']['planning']['correction_summary']={'enabled':settings.self_correction,'replans':replans,'recovered':success and replans>0}
        return {'success':success,'terminal':bool(final.get('terminal'))}
