"""English copy for the public refund summary; metrics remain evidence-derived."""
import re

COPY = {
'工作台': 'Workspace', '主站导航': 'Main navigation', '总览': 'Overview', '回放与回归': 'Replay &amp; regression', '基准测试': 'Benchmarks', '评估报告': 'Evaluation reports', 'Refund 评估': 'Refund evaluation',
'汇总指标公开展示。':'Aggregate metrics are public.',
'源码与数据集 ZIP 保留本地。':'Source and dataset ZIP files remain local.',
'合成场景 · 已验证执行证据':'Synthetic scenarios · Verified execution evidence',
'Refund 评估汇总':'Refund evaluation summary',
'从独立任务家族到数据划分，再到验证集与测试集的实际执行结果。':'Independent task families, dataset splits, and execution results on validation and test.',
'实测尚未完成':'Live evaluation incomplete', '实测完成':'Live evaluation complete',
'尚未完成 · 未测量':'Incomplete · Unmeasured', '跨模型实测尚未完成':'Cross-model evaluation is incomplete',
'等待至少两个真实模型及 provider 配置。离线接口测试不计入跨模型复现指标；未测量不等于 0%。':'At least two real models and provider configuration are required. Offline wire tests are excluded from cross-model metrics; unmeasured does not mean 0%.',
'跨模型真实调用评估':'Live cross-model evaluation',
'真实模型 Held-out 评估':'Real-model held-out evaluation',
'固定版本 · 按计划重复执行':'Pinned versions · Planned repetitions',
'成功 / 可判定执行':'Successes / decisive runs', '可判定 / 计划执行':'Decisive / planned runs',
'分母为两个模型所有重复执行均可判定的共同场景；分子要求至少两个模型的所有重复执行均失败。没有共同失败不代表没有其他失败模式。':'The denominator includes shared scenarios with decisive results for every repetition of both models. The numerator requires every repetition to fail for at least two models. No shared failure does not rule out other failure modes.',
'成功率按执行次数计算，调用错误与无法判定不计为业务失败。':'Success rates count executions. Provider errors and inconclusive results do not count as business failures. ',
' 次原始 JSON 解析失败，计入端到端任务失败。评估要求直接输出 JSON，未启用 JSON 强制格式，也未自动剥离 Markdown 代码围栏；因此这些失败不能单独解释为退款金额判断错误。':' raw JSON parsing failures, counted as end-to-end task failures. The evaluation requires plain JSON; it does not enforce JSON response mode or strip Markdown fences. These failures alone do not establish incorrect refund amounts. ',
'每个划分仅 2 个合成场景，不能推断生产泛化能力或 RL 训练收益。场景来源、verifier 资格和真实调用响应已在本地重新核验，公开页面仅含汇总指标。':'Each split contains only 2 synthetic scenarios. Results do not establish production generalization or RL training gains. Scenario provenance, verifier qualification, and live responses were reverified locally. This public page contains aggregate metrics only.',
'独立任务家族':'Independent task families', '分别采集的合成策略任务':'Separately captured synthetic policy tasks',
'可执行场景':'Executable scenarios','每个家族含 2 个变异场景':'2 mutated scenarios per family',
'已取得资格的 verifier':'Qualified verifiers','正例 + 7 类反例验证':'Positive witness + 7 negative witness types',
'带 reward 的记录':'Records with rewards','执行记录数，与场景数分别统计':'Execution records, counted separately from scenarios',
'数据划分':'Dataset splits','validation / test 均非空':'Nonempty validation and test',
'按来源轨迹、任务家族、父失败与等价任务整体分组。相关场景保持在同一划分；没有通过拆分兄弟场景填充 validation 或 test。':'Grouped by source trajectory, task family, parent failure, and equivalent task. Related scenarios remain in one split; sibling scenarios are never separated to fill validation or test.',
'仅 validation / test · 同一场景配对比较':'Validation / test only · Paired comparison on the same scenarios',
'规则候选成功数 / 场景数；比例分母为场景，不是执行记录':'Rule-candidate successes / scenarios; denominators count scenarios, not execution records',
'Broken 成功 / 场景数':'Broken successes / scenarios','Fixed 成功 / 场景数':'Fixed successes / scenarios',
'成功率变化':'Success-rate change',
'每个场景只有在该候选的所有重复执行均通过时才记为成功。validation 与 test 各含 2 个场景，样本量有限。':'A scenario succeeds only if every repeated run of that candidate passes. Validation and test each contain 2 scenarios; the sample is small.',
'这是合成环境中的规则候选对比。':'This compares rule-based candidates in a synthetic environment.',
'未进行 RL 训练，不能将上述变化解释为训练收益或真实模型能力提升。':'No RL training was performed. These changes do not demonstrate training gains or improved real-model capabilities.',
'变异：措辞调整 / 临时查询故障':'Mutations: wording changes / transient lookup faults',
'每个任务家族的数据划分、场景数、记录数和 verifier 资格状态':'Split, scenario count, record count, and verifier qualification for each family',
'Verifier 资格':'Verifier qualification',
'本页仅提供汇总。源码和完整数据集 ZIP 保留本地。':'Aggregate summary only. Source and full dataset ZIP files remain local.',
'Refund 合成任务家族、无泄漏数据划分和 held-out 规则候选评估。真实模型评估状态与指标。':'Synthetic refund families, leakage-free splits, held-out rule-candidate evaluation, and real-model metrics.',
'退款后的批准上限':'Approval cap after a refund','批准金额上限':'Approval cap','恰好剩余金额':'Exact remaining balance',
'大额支付':'Large payment','低剩余余额':'Low remaining balance','余额接近耗尽':'Nearly exhausted balance',
'已有部分退款':'Prior partial refund','剩余支付余额':'Remaining paid balance','退款后的新请求':'Request after a refund',
'请求金额上限':'Request cap','小额批准':'Small approval','小额请求':'Small request',
'Held-out 评估':'Held-out evaluation','任务家族':'Task families',
'未测量':'Unmeasured','条记录':'records','已通过':'qualified',
'跨模型失败复现：':'Cross-model failure reproduction: ',
' 个百分点':' percentage points',' 个家族 · ':' families · ',' 个场景':' scenarios',
'本轮有 ':'This run includes ',
'模型':'Model','划分':'Split','家族':'Family','场景':'Scenarios','记录':'Records',
}

def english(content):
    content = re.sub(r'OpenAI 固定版本模型 · 仅 validation / test · (\d+) 次计划执行，(\d+) 次取得业务判定。成功 (\d+)，失败 (\d+)，调用错误 (\d+)，无法判定 (\d+)。',
        lambda m: f'Pinned OpenAI versions · Validation / test only · {m[1]} planned runs, {m[2]} decisive runs. Successes: {m[3]}; failures: {m[4]}; provider errors: {m[5]}; inconclusive: {m[6]}. ', content)
    content = re.sub(r'其中 (\d+) 次因 OpenAI API 余额耗尽被拒绝，需补充额度后重新评估。错误不代表模型任务失败。',
        lambda m: f'{m[1]} calls were rejected because OpenAI API credits were exhausted. Add credits and rerun. Provider errors are not model task failures.', content)
    for source in sorted(COPY, key=len, reverse=True):
        content = content.replace(source, COPY[source])
    return content.replace('lang="zh-CN"', 'lang="en"')
