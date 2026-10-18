"""Research selection adapter for the existing PlanningAgent execution loop."""
import copy
from dataclasses import asdict, replace
from pathlib import Path
from statistics import mean
import time
from ...planning.contracts import PlanningConfig, CandidateAction, PredictionDistribution, ident
from ...planning.core import CandidateGenerator, PlanningFailure
from ...serialization import digest
from ...storage import save
from .risk_metrics import candidate_metrics, selection_score
from .rollout_sampler import MonteCarloSampler
from ..calibration.schemas import SuccessForecast, now
from ..horizon.replanning_policy import ReplanningPolicy
from ..horizon.prediction_error import state_error
from ..experiments.domain import policy, continuation, SCHEMAS, BENCHMARK_VERSION, TOOL_VERSION


class RiskAwarePlanner:
    version='probabilistic-planner.v1'
    def __init__(self,model,sampling,output,method='expected_value',risk_weight=.5,alpha=.9,uncertainty_weight=.2,replanning=None,budget=None):
        from ...planning.contracts import finite
        finite(risk_weight,'risk weight');finite(uncertainty_weight,'uncertainty weight');finite(alpha,'CVaR alpha',0,.999999)
        selection_score({'immediate_reward':0,'expected_reward':0,'return_variance':0,'loss_cvar':0,'epistemic_uncertainty':0},method)
        self.model,self.sampling,self.output=model,sampling,Path(output)
        self.method,self.risk_weight,self.alpha,self.uncertainty_weight=method,risk_weight,alpha,uncertainty_weight
        self.generator=CandidateGenerator(policy,SCHEMAS)
        self.continuation=CandidateGenerator(continuation,SCHEMAS)
        self.replanning=replanning or ReplanningPolicy()
        self.config=PlanningConfig(enabled=True,mode='active',self_correction=False,max_real_steps=68,max_uncertainty=1)
        self.cache={}
        self.shared_budget=budget

    async def plan(self,context):
        started=time.monotonic();session_id=ident();cutoff=now()
        cache=self.cache.get(context.trajectory_id)
        error=None
        if cache:
            prior=cache['last_prediction']
            observed=context.state
            numeric=state_error(prior,observed,('health',))
            error=max((v['absolute_error'] for v in numeric.values()),default=None)
        reason=self.replanning.trigger(cache['used'] if cache else 0,error,cache['uncertainty'] if cache else 0,
            bool(cache and cache['used']<len(cache['actions'])))
        session={'schema_version':'planning-session.v1','session_id':session_id,'planner_version':self.version,'created_at':cutoff,
            'benchmark_run_id':context.benchmark_run_id,'trajectory_id':context.trajectory_id,'task_id':context.task_id,'task_group':context.task_group,
            'origin':asdict(context),'origin_state_digest':context.state_digest,'planning_config':asdict(self.config),
            'world_model':{'id':self.model.model_id,'version':self.model.version},'candidates':[],'rollouts':[],'scores':[],
            'outcomes':[],'execution_kind':'HYPOTHETICAL_ONLY','status':'planning','real_execution':None,
            'budget':{'predictions':0,'model_calls':0,'tokens':0,'reserved_tokens':0,'cost_usd':0,'usage_complete':True}}
        try:
            if reason:
                previous_planning_id=cache['planning_id'] if cache else None
                candidates=await self.generator.generate(context,self.sampling.candidates)
                effective_sampling=replace(self.sampling,seed=(self.sampling.seed+context.state_version*104729)%2**31)
                sampled=await MonteCarloSampler(self.continuation,self.model,effective_sampling,self.shared_budget).sample(context,candidates)
                metrics=[]
                for candidate in candidates:
                    rr=[r for r in sampled['rollouts'] if r['candidate_id']==candidate.candidate_id]
                    summary=candidate_metrics(rr,self.alpha)
                    metrics.append({'candidate_id':candidate.candidate_id,**summary,
                        'score':selection_score(summary,self.method,self.risk_weight,self.uncertainty_weight)})
                selected=max(metrics,key=lambda m:m['score'])
                a=next(a for a in candidates if a.candidate_id==selected['candidate_id'])
                rr=[r for r in sampled['rollouts'] if r['candidate_id']==a.candidate_id]
                representative=max(rr,key=lambda r:len(r['steps']))
                cache={'actions':[CandidateAction(**s['action']) for s in representative['steps']],
                       'states':self._states(context.state,representative['steps']),'used':0,
                       'uncertainty':selected['epistemic_uncertainty'],'planning_id':session_id,'last_prediction':{},
                       'metrics':selected}
                self.cache[context.trajectory_id]=cache
                relative='research/'+session_id+'.json'
                record={'schema_version':'probabilistic-rollouts.v1','planning_id':session_id,'run_id':context.benchmark_run_id,
                        'task_id':context.task_id,'task_group':context.task_group,'trajectory_id':context.trajectory_id,
                        'origin_state_digest':context.state_digest,'origin_state':context.state,'simulated_only':True,
                        'world_model':session['world_model'],'sampling_config':asdict(effective_sampling),
                        'planner':self.method,'risk_weight':self.risk_weight,'alpha':self.alpha,'uncertainty_weight':self.uncertainty_weight,
                        'candidates':[asdict(a) for a in candidates],'candidate_evaluations':metrics,
                        'selected_candidate_id':a.candidate_id,**sampled}
                if self.shared_budget:
                    from ...serialization import canonical
                    self.shared_budget.consume(len(canonical(record).encode()))
                save(self.output/relative,record)
                session['budget']=sampled['budget']
                session['research']={'sidecar_path':relative,'sidecar_digest':digest(record),'planner':self.method,
                    'sampling_config':asdict(effective_sampling),
                    'candidate_evaluations':metrics,'replanning_event':{'reason':reason,'observation_version':context.state_version,
                        'observed_error':error,'prior_planning_id':previous_planning_id,
                        'trigger_threshold_status':'configured_synthetic_screening_not_production_validated'},'forecasts':[]}
                for candidate in candidates:
                    root=sampled['root_predictions'][candidate.candidate_id]
                    # Compact root distribution only; full H x M records remain associated.
                    session['rollouts'].append(self._compact(context,candidate,root))
                session['candidates']=[asdict(a) for a in candidates]
                for m,r in zip(metrics,session['rollouts']):
                    session['scores'].append({'imagination_id':r['imagination_id'],'candidate_id':m['candidate_id'],
                        'expected_task_reward':max(-100,min(100,m['expected_reward'])),'expected_execution_cost':m['expected_execution_cost'],
                        'predicted_failure_risk':1-m['success_probability'] if m['success_probability'] is not None else 1,
                        'uncertainty':m['epistemic_uncertainty'],'estimated_remaining_steps':self.sampling.horizon,
                        'estimated_success_probability':m['success_probability'],'score':m['score']})
                if context.state_version==0:
                    session['research']['forecasts']=self._forecasts(context,a,selected,cutoff)
                alternatives=[{'candidate_id':m['candidate_id'],'score':m['score'],'risk':m['severe_failure_probability'],
                               'expected_reward':m['expected_reward'],'loss_cvar':m['loss_cvar'],'uncertainty':m['epistemic_uncertainty']} for m in metrics]
            else:
                a=cache['actions'][cache['used']]
                predicted_state=cache['states'][cache['used']]
                delta={k:v for k,v in predicted_state.items() if context.state.get(k)!=v}
                # A sampled future is a point hypothesis, never a probability-one label.
                obs={'status':'completed','state_delta':delta,'terminal':predicted_state.get('terminal',False),
                     'success':predicted_state.get('success'),'side_effects':'memory_only'}
                root=PredictionDistribution(ident(),a.candidate_id,[{'observation':obs,'state_delta':delta,
                    'terminal':obs['terminal'],'success':obs['success'],'probability':None,'uncertainty':cache['uncertainty']}],
                    self.model.model_id,self.model.version,calibration_status='unavailable',metadata={'point_hypothesis':True})
                session['rollouts']=[self._compact(context,a,asdict(root))];session['candidates']=[asdict(a)]
                selected=cache['metrics'];alternatives=[{'candidate_id':a.candidate_id,'score':selected['score'],'risk':selected['severe_failure_probability']}]
                session['research']={'cached_plan_id':cache['planning_id'],'planner':self.method,'replanning_event':None,'forecasts':[]}
            cache['last_prediction']=copy.deepcopy(cache['states'][cache['used']]);cache['used']+=1
            self.generator.validate(a)
            session['selection']={'selected_candidate_id':a.candidate_id,'selected_action':asdict(a),'selection_score':selected['score'],
                'selection_reason':self.method+' over identical simulated candidate futures; executor policy remains authoritative',
                'alternative_candidates':alternatives,'predicted_risks':selected['severe_failure_probability'],
                'success_probability':selected['success_probability'] if reason else None,'calibration_status':'uncalibrated'}
            session['status']='selected'
        except Exception as exc:
            session.update(status='failed',reason=type(exc).__name__)
            if hasattr(exc,'budget'):session['budget']=copy.deepcopy(exc.budget)
            session['budget'].update(cost_usd=None,usage_complete=False)
        session['planning_latency_ms']=(time.monotonic()-started)*1000
        return session

    @staticmethod
    def _states(state,steps):
        current=copy.deepcopy(state);values=[]
        for step in steps:
            current.update(step['state_delta']);values.append(copy.deepcopy(current))
        return values

    @staticmethod
    def _compact(context,a,prediction):
        first=prediction['outcomes'][0]
        return {'imagination_id':ident(),'candidate_id':a.candidate_id,'origin_state_digest':context.state_digest,
                'simulated_steps':[{'action':asdict(a),'prediction':prediction,'sampled_outcome':first,'simulation_depth':0,'simulated':True}],
                'predicted_terminal_state':{**context.state,**first['state_delta']},'uncertainty':first.get('uncertainty',1),
                'termination_reason':'root_summary_full_futures_in_sidecar','synthetic':True}

    def _forecasts(self,c,a,metrics,cutoff):
        # The raw policy confidence is a declared uncalibrated heuristic, not an oracle.
        confidence=max(.5,min(.99,.98-.015*c.state['family']-.01*(a.action_payload.get('route',0))))
        values=[('raw_agent',confidence)]
        if metrics['success_probability'] is not None:values.append(('world_model_mc',metrics['success_probability']))
        return [asdict(SuccessForecast(ident(),c.benchmark_run_id,c.task_id,c.trajectory_id,c.task_group,
            self.version+':'+self.method,self.model.version,BENCHMARK_VERSION,c.environment_version,TOOL_VERSION,p,now(),cutoff,
            source=source,prediction_horizon=self.sampling.horizon,epistemic_uncertainty=metrics['epistemic_uncertainty'],
            provenance={'synthetic':True,'scenario_fingerprint':digest([c.task_group,c.state['domain'],c.state['family']]),
                'action_key':a.key,'source':'before_first_real_action'})) for source,p in values]
