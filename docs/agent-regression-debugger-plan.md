# Agent Regression Debugger：Codex 可执行改造计划

计划基于仓库 `j112929/agent-replay-mvp` 的提交 `e6a58768df22b6689cffffbf7214368ec4f98243`。这里只生成计划，没有修改仓库代码、提交、发布或调用付费模型。文中的新增路径、命令和接口都是目标规格，不代表仓库现在已经支持。

## 1. 产品决策与完成定义

保留 Python 包名 `agent-replay-debugger`、导入名 `agent_replay`、CLI `agent-replay`，产品展示名改为 **Agent Regression Debugger**。

一句话定位：**把生产 Agent 的一次失败，变成可在本地重跑、比较修复，并在 CI 中持续验证的回归用例。**

首批用户是能修改 Python agent 代码、有真实生产失败、愿意显式接入模型和工具边界的工程团队。交付主链路：

```text
生产 SDK 记录边界和失败
  → 导出可检查的 failure bundle
  → 原入口从头重跑，冻结已记录的外部边界
  → 修改模型 / 工具实现 / 代码，定位首次可观察分歧
  → 编写业务断言，把失败晋升为回归用例
  → 在每次 PR 中运行当前候选代码，生成 JSON / JUnit / HTML 报告
```

第一版必须完整实现这条链路；保留单步实验，但不把“复制历史输出”作为恢复成功的证明。

### 硬性产品语义

1. **执行完成 ≠ 任务正确。** `execution.status` 与 `evaluation.verdict` 独立。
2. **记录播放 ≠ 重跑。** 单步 recorded 模式只展示历史证据，不能作为 CI 通过的依据。
3. **从头重跑 ≠ checkpoint resume。** 入口代码、依赖和可恢复输入必须存在。
4. **冻结边界 ≠ 整个进程确定。** 没有接入的文件、数据库、网络、时间、随机和调度仍是缺口。
5. **首次分歧 ≠ 根因。** 报告列出证据和对齐置信度，不自动宣称找到 root cause。
6. **导入 trace 是数据操作。** 不从 trace、bundle 或 API 请求动态导入可执行模块。
7. **原失败永久保留。** 修复后的基线是新 artifact；不覆盖原 trace 来“变绿”。

### 首发范围

- 本地优先；生产端先写本地 spool，通过现有运维渠道传回 bundle。
- Python 同步/异步入口，显式工具装饰器与统一模型边界。
- frozen 全流程重跑、精确 fixtures、模型/工具/代码实验、结构 diff、业务断言、CI。
- 静态报告可离线查看；本机 runner 提供受控执行。

后置：托管生产采集服务、团队权限/计费、全框架自动接入、任意程序快照、流式/多模态原生回放、自动修复、failure clustering、自动 git bisect。第一版不承诺“capture any agent”或位级可重复。

## 2. 已核实的仓库基线

以下链接固定到审阅提交，避免后续默认分支变化导致计划失去依据。

| 已有文件 | 当前行为 | 改造决定 |
| --- | --- | --- |
| [capture.py](https://github.com/j112929/agent-replay-mvp/blob/e6a58768df22b6689cffffbf7214368ec4f98243/sdk/agent_replay/capture.py) | ContextVar、同步/异步装饰器、span、run.chat；退出 capture 才保存 | 保留公共入口，补 journal、输入/输出、failure、provenance、capture health |
| [schema.py](https://github.com/j112929/agent-replay-mvp/blob/e6a58768df22b6689cffffbf7214368ec4f98243/sdk/agent_replay/schema.py) | v1 验证、best-effort 脱敏、原子写 JSON；单 trace 最多 2,000 steps | 拆分存储/脱敏；增加 v2 和无损读取 v1 |
| [replay.py](https://github.com/j112929/agent-replay-mvp/blob/e6a58768df22b6689cffffbf7214368ec4f98243/sdk/agent_replay/replay.py) | 单步 recorded/fixture/live/tool；SDK replay_agent 从头重跑 | 保留兼容 facade，抽出策略、匹配、运行器、保真度 |
| 同文件 ReplayPolicy | 全表按 name/kind/脱敏 input 匹配并消费；同名 fixture 重复适用；异常统一 RecordedError | 增加作用域、occurrence、明确 fixtures 与异常映射；禁止含糊匹配 |
| [provider.py](https://github.com/j112929/agent-replay-mvp/blob/e6a58768df22b6689cffffbf7214368ec4f98243/sdk/agent_replay/provider.py) | 只支持非流式 OpenAI-compatible Chat Completions | 保留兼容 transport；统一 IR 后再接原生 Anthropic |
| [cli.py](https://github.com/j112929/agent-replay-mvp/blob/e6a58768df22b6689cffffbf7214368ec4f98243/sdk/agent_replay/cli.py) | 仅 serve 和必须 --step 的 replay；HTTP 只有 health/traces/replay | CLI 成为统一入口；HTTP 抽出成薄层 |
| [dist/core.js](https://github.com/j112929/agent-replay-mvp/blob/e6a58768df22b6689cffffbf7214368ec4f98243/dist/core.js)、dist/app.js | v1 import、单步 fork、字段 diff、localStorage、timeline | 增加 failure/experiment/regression 页面，保留原生 JS |
| [scripts/package_release.py](https://github.com/j112929/agent-replay-mvp/blob/e6a58768df22b6689cffffbf7214368ec4f98243/scripts/package_release.py) | 从 dist 复制到 sdk/web，再生成 ZIP | 明确 dist 为前端源码；SDK web 和 vendor 都自动同步 |
| [插件脚本](https://github.com/j112929/agent-replay-mvp/blob/e6a58768df22b6689cffffbf7214368ec4f98243/plugins/agent-replay/scripts/replay.py) | 独立实现 inspect/compare/demo，使用 vendor SDK | 合并能力到 SDK CLI，脚本改薄转发层，消除重复 diff |
| tests/test_sdk.py、tests/core.test.mjs | 8 项 Python + 5 项 JS 测试 | 保留作为兼容基线，再按能力拆测试 |

本次验证：`PYTHONPATH=sdk python3 -m unittest discover -s tests -v` 8/8 通过，`node --test tests/core.test.mjs` 5/5 通过。Python 为本机 3.14；涉及本机模拟 HTTP 服务的测试在允许 loopback 后通过。出现 HTTPError 对象清理 ResourceWarning，不是测试失败。未验证完整 Python 版本矩阵、真实模型、浏览器交互或发布构建。

审阅快照中没有 `.github/workflows/`，也没有仓库级 `AGENTS.md`。执行时仍须重新检查最新指令与提交差异。

## 3. 实施架构与文件归属

继续使用标准库、原生 JS 和本地文件。配置/用例采用 JSON，避免为了 YAML/TOML 解析增加运行依赖，也保持 Python 3.10 支持。开发依赖可添加 JSON Schema validator、wheel 构建和浏览器测试工具。

以下目录按阶段创建，不在第一阶段一次性空建所有模块。

```text
sdk/agent_replay/
  __init__.py                 [改] 兼容导出 + 新公开类型
  schema.py                   [改] validate/load 的兼容 facade
  capture.py                  [改] capture/span/tool/chat 公共 API
  replay.py                   [改] replay_step/replay_agent 兼容 facade
  provider.py                 [改] 旧 completion 的兼容 facade
  cli.py                      [改] 参数解析；不承载业务逻辑
  contracts.py                [新] dataclasses、枚举、序列化契约
  migrations.py               [新] v1 → v2 内存迁移
  serialization.py            [新] JSON 标准化、present/null、摘要规则
  redaction.py                [新] 脱敏策略和字段可恢复性
  storage.py                  [新] 原子文件、目录布局、哈希核验
  journal.py                  [新] append-only 事件与崩溃恢复
  provenance.py               [新] commit/dirty/dependency/env 标识
  bundle.py                   [新] 导出、导入、manifest、体积限制
  project.py                  [新] 读取本地注册表、路径约束
  runtime/
    __init__.py
    policy.py                 [新] frozen/fixture/live/local 决策
    matcher.py                [新] 稳定边界键、消费、歧义诊断
    fixtures.py               [新] 精确 selector、调用次数、返回/异常
    runner.py                 [新] entrypoint sync/async 生命周期
    worker.py                 [新] CLI 子进程协议、终止和结果写回
    fidelity.py               [新] 能力检查与保真度报告
    environment.py            [新] 显式 clock/RNG/state 适配点
    code_runner.py            [新] 不同 Git ref 的工作树和环境
  providers/
    __init__.py
    base.py                   [新] 统一 ModelRequest/ModelResponse 接口
    registry.py               [新] 本地 provider ID → 配置
    openai_compatible.py      [新] 迁移现有 HTTP transport
    anthropic.py              [新] 原生 Messages 适配，限定能力
    normalize.py              [新] IR ↔ provider 消息/tool call
  experiments.py              [新] ReplaySpec、矩阵、结果聚合
  comparison/
    __init__.py
    align.py                  [新] 树/序列对齐与 confidence
    diff.py                   [新] 结构 diff、噪声规则、分歧摘要
  regression/
    __init__.py
    cases.py                  [新] 用例加载、晋升、锁定基线
    assertions.py             [新] 纯声明式业务规则
    runner.py                 [新] 运行候选代码并评估
    reports.py                [新] JSON/JUnit/静态 HTML
  server.py                   [新] loopback API、任务注册和查询
  web/                        [生成] 禁止手改
schema/
  trajectory.schema.json     [保留] v1 历史契约
  trajectory-v2.schema.json  [新]
  replay-spec.schema.json    [新]
  comparison.schema.json     [新]
  regression-case.schema.json [新]
  regression-result.schema.json [新]
  bundle-manifest.schema.json [新]
  journal-event.schema.json  [新]
  project.schema.json        [新]
  fixture-set.schema.json    [新]
dist/
  core.js / app.js / styles.css / index.html / demo.js [改]
  api.js / compare.js / regressions.js / experiments.js [新]
tests/
  fixtures/v1/ fixtures/v2/ contract/ [新]
  test_schema.py test_capture.py test_journal.py test_bundle.py
  test_matcher.py test_runtime.py test_providers.py test_diff.py
  test_regression.py test_cli.py test_server.py test_code_runner.py
  test_release.py [新]
  core.test.mjs compare.test.mjs regression.test.mjs [改/新]
  e2e/ [新] 浏览器主流程
examples/regression_demo/      [新] 无外网、故障版/修复版、fixtures、用例
docs/                         [新] format、replay-guarantees、capture、ci、migration
.github/workflows/             [新] tests.yml、agent-regression.yml、release-check.yml
scripts/package_release.py    [改] 确定性构建 + --check
scripts/sync_plugin.py        [新] 同步 SDK 到 vendor
plugins/agent-replay/          [改] 文案、说明、入口、自动生成 vendor
```

依赖方向：capture → journal/storage/contracts；runtime → policy/provider/capture；experiments → runtime；regression → runtime + assertions + comparison；CLI/server → 这些应用服务。UI 不自行决定业务是否通过，不持有 provider key。

### 前端/插件构建规则

- 本次不迁移到 React/Next.js，不改变 Vercel 静态部署模型。
- `dist/` 保持当前源码地位；`sdk/agent_replay/web/`、插件 `vendor/` 是生成物。
- package_release 的资源清单加入新 JS，并覆盖 schema/docs/示例/CI 所需文件；同步更新 server 静态资源 allowlist 和 wheel package-data。
- ZIP 白名单生成，排除 traces、journals、bundles、reports、keys、.env、任意用户项目；禁止递归包含旧 ZIP。
- `--check` 在临时目录生成结果后比较文件列表及内容摘要；ZIP 时间戳固定，避免每次构建无意义变化。
- 插件脚本最后只调用同一 `agent_replay.cli.main()`；所有业务测试对源码 SDK 和 vendor smoke 各跑一次。

## 4. 数据模型：轨迹、实验和判定分离

版本策略：新增 trajectory `schema_version: "2.0"`；新 ReplaySpec/Comparison/Case/Result 各自 `schema_version: "1.0"`。Python 类型和 JSON Schema 保持一致；JS 用同一组 contract fixtures 验证等价行为。

### 4.1 Trajectory v2

| 字段 | 类型/规则 |
| --- | --- |
| schema_version, id, name, started_at | 必填；ID 无路径语义；UTC ISO 时间 |
| execution | `{status: running\|completed\|error\|interrupted, duration_ms, error?}` |
| input / output | `{present: bool, value?: JSON, replayability: complete\|redacted\|truncated\|unsupported, artifact_ref?: string}`；区分没有值和 null |
| steps | `Step[]`，开始顺序；事件序号负责落盘顺序，不能用时间戳推断因果 |
| failures | `Failure[]`，可有多个；错误执行和业务失败分别记录 |
| provenance | `{sdk_version, python_version, platform, code, dependencies, config_digest, prompt_versions, capture_profile}` |
| capture_health | `{state: complete\|partial\|failed, dropped_events, persistence_errors, redacted_paths, truncated_paths, last_durable_seq}` |
| replay | 可选 `{source_trace_id, source_digest, spec_id, scope, mode, source_step_id?}` |
| fidelity | 可选 `{level, capabilities, gaps, matched_steps, unmatched_steps, ambiguous_steps, unused_recordings}` |
| evaluation | 缺省 `{verdict: not_evaluated}`；业务判定引用独立 Result，不能由 trace.completed 推出 pass |
| tags / metadata | 有界 JSON；metadata 不承载可执行指令或凭据 |

`code = {repository?, commit?, dirty: bool?, dirty_diff_digest?, entrypoint_id?, source_available: bool}`。dirty 不默认为 false，未知为 null。默认只记录 diff 摘要，不复制工作区秘密；显式生成 bundle 时可选择加入审阅过的补丁。

`dependencies = {python, lockfile_digests, image_digest?, replay_engine_version}`。不收集整个环境变量字典；配置只记录白名单值或引用，敏感项记录缺口。不能把“某 lockfile 的摘要”称为已重建环境。

### 4.2 Step / Failure

```text
Step:
  id, parent_id?, seq, kind(agent|llm|tool), name
  boundary_id?                 # 用户显式稳定逻辑 ID
  scope_path[], lane_id?       # 逻辑祖先/显式并发通道，不使用随机 run ID
  occurrence, attempt         # 同作用域调用序号、重试次数
  execution {status, start_ms, duration_ms}
  input/output: ValueEnvelope
  error? {type, message, code?, replay_mapping_id?}
  model? {provider, requested, resolved?, parameters, seed?, fingerprint?}
  request? / response?         # 脱敏后的原生数据和统一 IR 引用
  tool? {implementation_id?, version?, side_effect: none|read|write|unknown}
  provenance? {source_step_id?, resolution: captured|frozen|fixture|live|local}
  usage? {input_tokens?, output_tokens?, total_tokens?, source: measured|historical}

Failure:
  id, category(exception|assertion|manual|timeout|capture_incomplete)
  step_id?, assertion_id?, message, observed_at
  evidence[]                  # step ID + JSON Pointer，不复制整个 payload
  status(open|case_created|resolved), regression_case_id?
```

Failure 的后续状态变化单独存 annotations，原 trajectory 不原地重写。业务违规可发生在正常 completed 的 trace，例如没有抛异常但退款 299 元。

### 4.3 Journal 和 failure bundle

Journal：每条 `{schema_version, run_id, seq, type, timestamp, payload, checksum}`，事件类型 `run_started / step_started / step_completed / step_failed / run_output / failure_observed / run_finished`。`checksum` 检测意外损坏，不宣称验证来源真实性。

Bundle manifest：`{schema_version, bundle_id, source_trace_id, created_at, files:[{path, sha256, size, media_type}], code_ref?, redaction_policy_digest, replay_requirements, capture_health}`。内容至少含轨迹、必要 fixtures、环境标识；源码/依赖不是默认打包对象。缺源码时只允许 inspection 或有证据支持的单步实验。

### 4.4 ReplaySpec 与 Experiment

```text
ReplaySpec:
  schema_version, id, source_trace_id, source_digest
  scope: step|agent; step_id?; entrypoint_id?; project_id?
  policy: frozen|hybrid
  tool_rules[]: {selector, mode: frozen|fixture|local, fixture_id?, implementation_id?}
  model_rules[]: {selector, mode: frozen|live, provider_id?, model?, parameters?}
  fixture_set_digest?, code_ref?, prompt_overrides?
  limits: {timeout_seconds, max_steps, max_model_calls, max_tokens?, max_cost_usd?}
  determinism: {clock_fixture?, rng_seed?, state_adapter_id?, environment_profile?}

Experiment:
  id, spec_id, source_trace_id, candidate_trace_id?
  state: queued|running|completed|failed|cancelled|interrupted
  execution_status?, evaluation_result_id?, comparison_id?, error?
  started_at?, finished_at?, worker_exit_code?, artifacts[]
```

selector 统一为 `{boundary_id?, kind?, name?, scope_path?, lane_id?, occurrence?, input_digest?}`。多个规则命中同一调用则配置错误；不靠“后写覆盖前写”掩盖冲突。provider、entrypoint、工具实现和 state adapter 均引用可信项目注册表里的 ID。

### 4.5 Comparison

包含 source/candidate ID 与摘要、spec 差异、coverage、`aligned_pairs[]`、`added_steps[]`、`removed_steps[]`、`ambiguous_groups[]`、`field_changes[]`、`first_observed_divergence`、执行/业务结果差异、指标差异。

字段 diff 使用 JSON Pointer；每条 `{path, before_present, after_present, before, after}`，正确区别缺失/null/false/0。对齐 confidence 为 `exact / inferred / ambiguous`，依据记录在每对中。并发情况下 first divergence 可以是一个无先后关系的集合，而非伪造唯一“第 8 步”。

### 4.6 RegressionCase / RegressionResult

```text
RegressionCase:
  schema_version, id, title, tags[], owner?
  source: {trace_path, sha256}        # 原生产失败，不可变
  entrypoint_id
  replay_spec: {...}                 # 不固定候选代码 commit，CI 使用当前检出
  assertions[]
  baseline?: {result_path, sha256, trace_sha256}
  live_policy?: {repetitions, min_pass_rate}

RegressionResult:
  schema_version, id, case_id, candidate_commit?, case_digest, source_digest
  replay_spec_digest, fixture_digest?, engine_version, baseline_digest?
  execution_status
  verdict: pass|fail|inconclusive|error|not_evaluated
  assertions[]: {id, verdict, expected, actual, evidence, reason?}
  fidelity, comparison_id?, duration_ms, model_usage?, artifacts[]
```

`pass` 必须实际执行候选入口、完成所有必需断言，并满足 replay 的最低证据要求。baseline 是审阅过的成功参考，不是 pass 的唯一依据；原始 failure source 从不变成成功参考。

### 4.7 v1 迁移

- 原 JSON 保留；load 时迁移内存副本，记录 `migrated_from: "1.0"`。
- 原 success → execution.completed；`application_validated: false` → not_evaluated；即便旧值为 true，没有断言证据也不能升级为新协议 pass。
- 缺 input/output、代码版本、boundary_id、原生 model request 等填 unknown/gaps，不填“合理默认”冒充历史。
- v1 缺父节点的单步 fork 可迁移为断开的根并附 warning；v2 对父引用、环、重复 ID、负数/非有限数严格拒绝。
- 对缺边界信息的 v1 只在 name/kind/input 唯一时支持弱匹配；重名同参且缺作用域时返回 ambiguous。
- CLI 保留旧 `replay TRACE --step ID --fixture/--model/--tool`；旧 SDK 入口保留兼容返回对象和必要旧字段，deprecated 字段只用于旧调用，不让新逻辑依赖它。
- 保留 v1 浏览器 localStorage key；v2 使用新 key，先验证迁移成功再切换，不删除原数据。旧静态页面读取 v2 不受保证，发布时同步所有渠道。

## 5. Production failure capture

### API 目标

```python
from agent_replay import capture, tool

@tool(name="orders.lookup", boundary_id="orders.lookup", side_effect="read")
def lookup_order(order_id):
    ...

with capture(
    "refund-agent",
    input={"order_id": "o_1042"},
    entrypoint_id="refund",
    durability="buffered",
    on_capture_error="warn",
    redactor=my_redactor,
) as run:
    result = refund_agent(run)
    run.set_output(result)
    if result["amount_minor"] > 3000:
        run.fail("refund exceeds policy", category="assertion")
```

兼容原有 `capture(name, directory, metadata)` 和 `agent(run)`。新入口通过 `run.input` 获取规范化输入；业务 SDK 返回值保持原类型，不因持久化序列化而改变应用结果。

### 落盘与失败保留

- 每个 run 单独 journal；线程锁串行分配 seq/写入；ContextVar 管理逻辑父级，不宣称自动支持多进程/分布式 trace 拼接。
- `buffered`：每个边界写入并 flush，失败/结束 fsync；报告仅承诺最近 durable seq 之前的内容。`sync`：每条事件 fsync。异步业务中将阻塞持久化成本明确测量，初版不做无限后台队列。
- capture 开始即记录运行输入、代码/环境标识。正常结束物化 trajectory JSON；异常结束也保存，保留原异常传播语义。
- 进程被杀时，通过有效 journal 前缀恢复 `interrupted` 和未完成 span。没有 step_completed 的外部写入，其是否已经发生为 unknown；绝不自动再发一次。
- 恢复仅忽略尾部不完整行；中间 checksum/seq 损坏导致 partial 和人工检查，不能偷偷跳过。
- `on_capture_error=warn` 不掩盖业务异常；记录可观察 capture health。测试/CI 可选 `raise`。磁盘满时不会承诺完整保存。
- 默认保存完整已接入 run；按成功/失败保留策略在结束后回收。不能仅在失败发生时才开始采样，丢掉前序证据。
- 增加按天/字节的显式 cleanup 命令或库函数；被已晋升 case 引用的 artifact 受保护。初版不自动后台删除。

### 脱敏与可恢复性

- 脱敏在任何 journal/blob 写入前执行；自定义 hook 抛错时按 capture error 策略处理，不回退为明文。
- 标记 redacted/truncated/unsupported，保留可见路径；不可恢复值不能直接参加严格重跑。
- 初版只对完整非敏感标准 JSON 计算匹配 input_digest。脱敏后的同一 `[REDACTED]` 不能使两个不同调用被视为相同；需要显式 fixture 或可信本地输入注入。
- 不把原始 secret 的普通 SHA256 当匿名化处理；匹配所需敏感值不写普通哈希。
- 原生 provider 请求/响应先脱敏，错误文本和 headers 同样受策略约束。
- 体积限制必须显式记录截断，不能截断后继续声称可复现。保留当前单文件 5 MB / 2,000 步兼容上限；v2 大 artifact 可进 bundle，首版总解包上限建议 50 MB 并配置化。

## 6. deterministic-ish replay 的执行规则

### 三种证据级别

| level | 含义 | 是否可用于默认离线 CI |
| --- | --- | --- |
| observation_only | 只复制单步历史输出/异常 | 否 |
| controlled_boundaries | 当前代码已从头执行，声明的工具/模型边界被冻结或显式替换；缺口列出 | 满足 case 要求且全部断言完成时可以 |
| live_variant | 至少一个真实模型/外部边界调用 | 单列 live suite，不假称确定性 |

### 匹配与严格策略

1. 先定位 `boundary_id + scope_path + lane_id + occurrence + attempt`，再核验 kind 与输入。省略 boundary_id 时退化到受限 name 匹配，并记录置信度下降。
2. 字典 key 排序；数组顺序保留；数值采用明确跨语言 canonical JSON 规则，禁止 NaN/Infinity。超出 JS 安全整数范围的值要求类型化字符串或拒绝，不生成 Python/JS 不同摘要。
3. 每个 recording 默认消费一次。顺序重复调用按 occurrence；并发同名同参必须有 lane/call key，不能依赖网络完成顺序。
4. 未匹配、歧义、输入红删或缺失直接返回 ReplayMismatch / ReplayUnavailable；从不 fallback 到真实工具。
5. 工具新参数需要 fixture/local 实现规则；不为了让新分支走下去而把旧结果硬塞给新参数。
6. 有 `source_step_id` 的匹配证据写入候选 step。未使用的旧记录出现在 coverage 中：相同代码复现时可视为不完整；明确的代码变更实验中可以是预期路径变化，由断言判断业务结果。
7. 旧同名 overrides 保持旧调用语义并发出诊断；新 ReplaySpec 禁止隐式同名无限复用。

### FixtureSet

每条 `{id, selector, response: {kind: return|raise, value?, error?}, consume: once|sequence|repeat, expected_calls?}`。sequence 消费有序结果；repeat 必须显式配置。无匹配或多匹配均错误。支持 JSON null 作为正常输出。

写工具应配置可观察的 test double：记录新调用参数，返回明确模拟响应，业务规则评估候选调用金额。不能执行生产退款 API 来验证修复。

异常映射使用可信项目注册的 `error_mapping_id`；内置受限异常构造器只接受明确字段。未知异常仍用 RecordedError 并记录 fidelity gap；禁止按 trace 中 module 名任意 import。严格依赖异常类型的 case，在未映射时 inconclusive，不能“重现成功”。

### 入口与运行环境

- Python SDK 提供 sync `replay_agent()` 与真正可 await 的 `async_replay_agent()`，避免活动 event loop 中嵌套 asyncio.run。
- CLI/server 在子进程中执行，记录真实超时/取消，终止进程组并保存 interrupted 结果。重启 runner 后旧 running job 变 interrupted，不自动重跑外部调用。
- 提供显式 `run.clock`、`run.random`、state adapter；记录 seed 不等于控制所有随机源。不 monkeypatch 全进程时间/网络来制造虚假 sandbox。
- 默认本地执行可信项目；若选择容器 profile，限制网络、工作目录和挂载，并记录镜像 digest。子进程隔离本身不是安全沙箱。
- `doctor` 输出入口/代码/依赖/fixtures/异常映射/模型协议/缺口；缺必要条件时先失败，不运行到一半再猜。

## 7. cross-model / tool / code 实验

### 模型：先协议抽象，再原生第二家

`ModelRequest = {messages, tools, tool_choice?, generation, provider_options}`；消息由 text/tool_call/tool_result block 构成，响应为统一 assistant blocks、finish_reason、usage、原生 response。保留 provider 原文用于审计，但比较使用规范化字段。

- 保留 `run.chat()` 的 Chat Completions 返回结构与语义。新增 `run.model(request, provider_id=...)` 返回统一 ModelResponse，原生跨 provider 的完整 agent 重跑以这个边界为准。
- OpenAI-compatible 现有逻辑搬入 providers/openai_compatible.py；provider.py 继续转发。
- Anthropic 原生适配首先只支持非流式 text、system、多轮 tool use/result、明确 token limit；实现时核对官方 API 文档，固定测试 fixture 的协议版本。
- unsupported system blocks、图片、stream、参数映射或 tool schema 在预检返回能力错误；不静默丢参数，不把“换 base_url”宣传为跨原生协议。
- seed、resolved model、provider fingerprint 仅在供应商提供时记录；temperature=0 不保证复现。
- 模型切换引起工具名/参数变化时，保持严格工具策略；若没有精确 fixture/已注册 test double，报告分歧处的 inconclusive，不继续真实写操作。
- 第一版 default 单变量实验；矩阵是明确 variants 列表，不自动笛卡尔积。每个 variant 记录实际请求参数和重复次数。
- 成本缺 provider usage/价格配置时为 unknown，不能填 0；设置 max_cost_usd 但无法估算上限时拒绝启动。确定性硬上限优先使用请求次数、token 输出上限和超时。

### 工具实现

可信项目配置 `{implementation_id: {callable: "package.module:function", side_effect, version}}`；CLI 指定 implementation ID，记录实现版本/摘要。local 工具函数获得候选真实参数，不从原 trace 复制旧参数。

默认冻结其余边界。新工具内部自行访问文件/网络不自动变成受控行为；profile 必须表明这一点。UI/API 不接受任意 `module:function`。旧 CLI `--tool` 显式执行语义保留，但新实验推荐注册 ID。

### 代码版本

- `--commit REF` 只解析本地已有 ref 到完整 SHA；不隐式 fetch、checkout 用户工作区或执行安装脚本。
- 用分离 detached worktree；候选代码入口在自己的 cwd/解释器中运行。项目环境定义映射 lockfile/镜像或已准备 interpreter，避免只改 commit 字段却仍执行当前进程已导入模块。
- engine 用固定控制器版本，候选应用依赖用各自环境；报告同时记录两者，不能误加载候选仓库中过旧 SDK。worker handshake 校验协议/SDK 能力，不兼容时给 setup error。
- 不复用主进程 sys.modules；tests 用两个 commit 返回不同 marker 证明真正切换代码。
- 缺入口、缺依赖、dirty patch 缺失、不可用原始 commit 均报 unavailable；不宣称复现原版环境。
- CI 的默认候选是当前 checkout，包括本地允许的未提交修改；baseline 是已固定 artifact。`--commit` 用于显式历史比较，不会让 CI 自动回到旧成功代码。
- 自动安装依赖必须使用项目显式声明的 setup profile，作为可执行项目代码处理；不执行 bundle 自带 install 指令。

## 8. diff：结构变化与业务变化分开

### 对齐算法

1. 优先使用来源 step 绑定或相同稳定 boundary/scope/lane/occurrence。
2. 对剩余节点按父级作用域和 kind/name 作有界序列对齐，候选匹配记为 inferred。参数不是对齐必需相等项，否则“退款金额变更”会只表现成删一行加一行。
3. 并发 lane 各自对齐；无法确定则返回 ambiguous group。未对齐的步骤保留 added/removed，不强行按数组下标配对。
4. 同时展示 input/output/error、模型参数、工具版本、代码版本与新增/消失分支。
5. 忽略随机 step ID、绝对时间等比较噪声；默认不忽略业务输出。ignore paths/数值容差必须在 case/spec 中声明，显示在报告。
6. latency/token 分开标明 historical/measured；frozen 回放耗时不用于证明模型变快，单次实测差异不外推性能结论。

首发不使用 LLM judge 判定“语义等价”。CI 真值来自明确业务断言；以后可以加可选 judge，但必须独立记录版本、调用成本、不确定性。

### 报告最低信息

source/candidate、实际执行范围、被改变量、代码/环境、保真度缺口、首次可观察分歧、added/removed/ambiguous、断言及证据、最终输出、取消/超时、复现命令。UI 和 JSON 使用同一 Comparison；托管静态 UI 的本地比较只实现相同契约，并用共享 golden fixtures 验证一致。

## 9. Regression CI：用业务规则防止假绿

### 第一版声明式 assertion DSL

支持 `equals / not_equals / exists / lte / gte / contains / count / forbidden / execution_completed / no_unhandled_error`。取值范围为 trajectory output、候选 steps、工具输入/输出、execution。路径用 JSON Pointer，不用 eval/任意 Python 表达式。

- 选择器零命中时，数值/equals 断言 fail；证据因截断/不可重放而缺失则 inconclusive。`forbidden` 只有实际覆盖目标运行范围时零命中才 pass。
- bool 不按整数比较；缺值和 null 区分；多值显式 quantifier `all/any/exactly`，不能悄悄只取第一个。
- 不只写“refund amount <= limit”；还要断言完成、恰好一次退款和目标订单，否则“根本没退款”会假绿。
- 自定义 Python evaluator 后置；如加入，只允许可信 registry ID，在 worker 内执行，异常 → error，不当作 pass。

示例文件 `examples/regression_demo/cases/refund-limit.case.json`（目标规格）：

```json
{
  "schema_version": "1.0",
  "id": "refund-limit",
  "title": "退款不得超过批准额度",
  "tags": ["offline", "refund"],
  "source": {"trace_path": "../traces/failure.json", "sha256": "<生成时计算真实摘要>"},
  "entrypoint_id": "refund",
  "replay_spec": {
    "scope": "agent",
    "policy": "frozen",
    "fixture_set": "../fixtures/refund.json",
    "limits": {"timeout_seconds": 20, "max_steps": 100, "max_model_calls": 0}
  },
  "assertions": [
    {"id": "completed", "op": "execution_completed"},
    {"id": "one-refund", "select": {"boundary_id": "payments.refund"}, "op": "count", "expected": 1},
    {"id": "amount-limit", "select": {"boundary_id": "payments.refund"}, "path": "/input/value/amount_minor", "op": "lte", "expected": 3000, "quantifier": "all"},
    {"id": "correct-order", "select": {"boundary_id": "payments.refund"}, "path": "/input/value/order_id", "op": "equals", "expected": "o_1042", "quantifier": "all"}
  ]
}
```

Case 内嵌的是 ReplaySpec 模板：允许相对 fixture_set 路径；加载时验证根目录约束、读入 fixture、计算 digest、补 source/spec ID，生成不可变完整 ReplaySpec 后执行。所有相对路径均以 case 所在目录解析，不依赖 shell cwd。

Demo 不冻结原错误写工具响应来迁就新参数：对 `payments.refund` 配置显式 repeat test fixture/spy，返回模拟成功并捕获候选参数。错误代码输出 29900，修复代码输出 2900；四条断言验证修复结果。金额用整数最小货币单位，避免浮点歧义。

### 晋升与基线

1. `case create` 从 failure 生成 source 固定副本、摘要和 draft case。草稿缺业务断言时不能运行成 pass；不得默认 snapshot 当前失败输出为“正确”。
2. 用户编写/审阅断言，用故障代码跑出 fail，再用修复代码跑出 pass。
3. `case accept` 只能接受实际完整重跑、全部必需断言通过的结果，建立 baseline lock；不能接受 recorded/单步 fixture 的成功。
4. 已有 baseline 更新必须显式执行 accept；`test` 永不自动更新。审阅 diff 同时显示断言本身是否变宽。
5. case/fixture/source/assertion 任一摘要变化则旧 baseline 过期，报告 stale；允许重新运行评估，但不能把旧 baseline 当现行证据。

### CI 与退出码

新命令统一：`0` 请求成功且 test 全 pass；`1` 至少一个业务断言 fail；`2` 输入/配置/基础设施错误；`3` 证据不足、mismatch、ambiguous 或严格模式下的 skipped；`130` 用户取消。混合 suite 优先级 `130 > 2 > 3 > 1 > 0`，报告仍保留所有 case 结果。

旧单步 replay 保持原退出码 0/1/2。新 run 中 agent error 若执行证据完整且违反 execution_completed 是 fail；provider 无法访问、worker 崩溃等导致无法判定时为 error。failure trace 正常结束但业务违规也是 fail。

`test` 空 suite、零条启用断言、没有候选入口实际执行均不得返回 0。JUnit：fail → failure，error → error，inconclusive → skipped 并同时返回 3；CI 看退出码，不能只看 skipped 数。

`.github/workflows/agent-regression.yml`：PR 运行离线用例，禁用模型调用，不注入 provider 凭据，权限只读；始终上传结果 JSON/JUnit/HTML。代码/断言/fixture 变更都触发；artifact 只含经过脱敏的测试数据。live suite 单独手动触发或可信主分支调度，记录 n 次结果和 pass_rate；不向 fork PR 提供 secrets，不使用 pull_request_target 执行未信任代码。

Live 默认 repetitions=5 只是探索值，不是统计保证。min_pass_rate 由 case 明确指定；需要数据完整，服务错误不能从分母悄悄剔除。真实供应商 smoke 由环境变量配置，未配置则明确 skipped，不计为已验证跨供应商上线能力。

## 10. CLI 目标契约

CLI 的 inspect/compare/demo 从插件迁入 SDK，插件保持别名兼容。输出人读摘要默认写 stdout；`--json` 输出稳定 JSON，诊断走 stderr；JSON 中不能混入进度文本。

```bash
# 无代码执行
agent-replay inspect failure.json --json
agent-replay doctor failure.json --project agent-replay.json --entrypoint refund
agent-replay bundle export failure.json --output failure.arb.zip
agent-replay bundle import failure.arb.zip --directory .replay
agent-replay recover .replay/journals/RUN_ID.jsonl

# 保留现有单步行为
agent-replay replay failure.json --step EXACT_STEP_ID
agent-replay replay failure.json --step EXACT_STEP_ID --fixture replacement.json

# 新全流程命令；从头重跑当前项目入口
agent-replay run failure.json --project agent-replay.json --entrypoint refund --policy frozen --json
agent-replay run failure.json --project agent-replay.json --spec specs/tool-fix.json --json
agent-replay run failure.json --project agent-replay.json --spec specs/model-b.json --json
agent-replay run failure.json --project agent-replay.json --entrypoint refund --commit FIX_COMMIT --policy frozen --json
agent-replay experiment failure.json --project agent-replay.json --variants specs/variants.json
agent-replay compare original.json candidate.json --output report.json

# 晋升、验证和基线
agent-replay case create failure.json --entrypoint refund --output tests/agent_cases/refund.case.json
agent-replay test tests/agent_cases --project agent-replay.json --report-dir .replay/reports
agent-replay case accept tests/agent_cases/refund.case.json --result .replay/reports/RESULT_ID.json
agent-replay serve --project agent-replay.json
```

约束：`run` 的 --spec 与零散 model/tool override 互斥；entrypoint 可由 spec 或参数提供，二者冲突报错。`experiment` variants 是显式 spec 列表，逐项稳定编号；用户配置并发上限，默认 1。CLI 支持 --help 中清楚显示“执行 agent code”与“只读 inspection”。

本地 `agent-replay.json` 包含 schema_version、project_id、project_root、entrypoints、providers、tools、error_mappings、environment_profiles。provider 配置只保存 `api_key_env` 等环境变量名称。不可将导入 bundle 的同名文件自动提升为可信项目配置。

新存储：`.replay/traces/`、`journals/`、`experiments/`、`comparisons/`、`reports/`、`bundles/`、`worktrees/`；提交到 Git 的 case/source/fixtures 位于项目 tests/agent_cases，使用脱敏最小数据。不要求用户把整个 `.replay/` 提交。

## 11. API：本机执行服务，不是托管 ingestion

把 make_handler 移到 server.py，cli.py 保留旧导入兼容。继续 loopback、Host/Origin 校验、大小限制、无目录浏览；新 API 使用 `/api/v1`，旧三个 endpoint 通过兼容层保留一个小版本。

| Method/path | 请求 | 响应/行为 |
| --- | --- | --- |
| GET /api/v1/health | — | version、schema_versions、capabilities、project_id、限额 |
| GET /api/v1/traces?cursor=&limit=&failure= | 过滤 | summaries + next_cursor，不每次传全量轨迹 |
| GET /api/v1/traces/{id} | — | 单 trace，超大 payload 用 artifact 引用 |
| POST /api/v1/traces/import | 已解析的 trace JSON | 201 + id/diagnostics；同 ID 同摘要复用，不同摘要 409 |
| GET /api/v1/failures | cursor/status | failure 摘要及 case 关联 |
| POST /api/v1/experiments | source_id、完整 ReplaySpec | 202 + job_id；只允许已注册入口/工具/provider |
| GET /api/v1/experiments/{id} | — | state、result、coverage、错误、artifact IDs |
| POST /api/v1/experiments/{id}/cancel | — | 请求取消；最终结果异步变更 |
| POST /api/v1/comparisons | source_id、candidate_id、rules | Comparison ID + 内容 |
| GET /api/v1/comparisons/{id} | — | Comparison |
| POST /api/v1/cases | source_id、entrypoint_id、assertions | 保存 draft；校验生成目标目录 |
| GET /api/v1/cases | — | 摘要、draft/stale/validated |
| POST /api/v1/cases/{id}/accept | result_id | 验证完整结果后写 baseline |
| POST /api/v1/suites/run | registered_suite_id | 202 + suite job_id |
| GET /api/v1/suites/{id} | — | suite 状态与各 case verdict |
| GET /api/v1/artifacts/{id} | — | 注册 artifact，只能访问存储根内资源 |

所有错误 `{error: {code, message, details?, request_id}}`；400 无效输入，403 非本机/无会话权，404 未知 ID，409 冲突或错误状态，413 过大，422 replay capability 不满足，429 runner 队列满，500 内部错误。执行结果里的业务 fail 与 HTTP 请求失败分开。

变更请求使用启动时生成的本机会话 token（bootstrap 同源页面后获取），保留 Host/Origin 检查，拒绝 text/plain 变更请求。token 不进 trace/URL/provider 配置，不宣称提供多用户认证。API 不接收 arbitrary base_url、文件路径、Git shell 命令或动态 callable；这些选项来自本地受信任配置。

任务状态落盘；默认 1 worker、有界队列。POST 可带 Idempotency-Key，相同 key+body 返回同 job，不同 body 409；不要因为浏览器超时重发模型调用。取消后外部 provider 是否完成可能未知，记录调用状态，不能承诺撤销已经发生的外部效果。

## 12. UI 改造

继续原生 ES modules。app.js 保留路由/状态，新增模块分别渲染比较、实验、回归；core.js 承载纯验证/通用结构函数，避免再长成单文件状态机。

### 页面与主流程

1. **Failures**：优先展示异常和业务断言失败；列 agent、失败类型、代码版本、capture completeness、关联 case。completed 且 assertion fail 也进入列表。
2. **Failure detail**：保留 timeline/inspector，增加输入/代码/环境/缺口，以及「检查能否重跑」「创建实验」。单步动作明确标“单步”，全流程标“从入口重跑”。
3. **Experiment setup**：显示 source、入口、frozen 边界、改动变量、fixture selector、预算；基于 health capabilities 禁用不可用项并解释缺什么。预检结果先展示，执行使用注册项目。
4. **Compare**：左右同步 timeline；顶部固定执行判定、业务判定、保真度；默认定位首次分歧，能切换新增/消失/歧义。参数和输出同时可见，不能只比较最终文本。
5. **Create regression**：从证据路径生成 assertion 草稿、编辑 expected 值、要求实际业务断言；显示“故障版失败 / 修复版通过”证据。单步 fixture 不能直接晋升成功 baseline。
6. **Regressions**：case 列表、最近候选 SHA、pass/fail/inconclusive/error、过期基线、下载报告与 CI 接入说明。

静态托管模式：可导入/查看/比较/导出报告与 draft case；不能执行入口、运行 live 模型、写本地 suite。localStorage 只作便利缓存，持久结果通过导出或本机文件保存。使用未知/未验证标签，不能因为载入 demo 显示“生产已恢复”。

统一转义所有 trace 文本、JSON 与错误；报告生成同样防 HTML 注入。键盘完成导入→选择→比较，窄屏可切换单侧；大 trace 虚拟化或分页，不直接渲染 2,000 × 深层 JSON。导入失败必须原子，不污染已有 workspace。

## 13. 测试与防假实现清单

| 模块/测试文件 | 必测场景 | 验收依据 |
| --- | --- | --- |
| test_schema.py + contract/ | v1 保留、v2 roundtrip、缺失/null、父引用/环/NaN、未知版本 | Python/JS 对同一 corpus 接受/拒绝一致 |
| test_capture.py | sync/async、嵌套、线程 seq、业务失败但正常完成、返回值不被序列化替换 | 正确 parent 与输出；不改变原异常 |
| test_journal.py | 子进程完成边界后被杀、尾行断裂、盘满、序号损坏、重复恢复 | 只恢复有效前缀；partial 绝不 complete |
| test_bundle.py | hash 不符、ZIP traversal/symlink/zip bomb、丢文件、敏感路径 | 导入不执行代码；存储根外零写入 |
| test_matcher.py | 重名重复调用、不同父级、异步 lane、参数变化、脱敏碰撞、fixture 耗尽 | 歧义和 mismatch 停止，真实工具计数不增加 |
| test_runtime.py | 从头入口、async 活动 loop、异常映射、clock/RNG、超时/取消 | 子进程结束并保存完整诊断；已匹配数据可追溯 |
| test_providers.py | OpenAI/Anthropic 本机 mock、多轮 tool result、unsupported、429/超时、key 脱敏 | 实际请求结构与响应规范化正确；不静默降级 |
| test_diff.py + compare.test.mjs | 插入/删除、并发 reorder、参数变化、ambiguous、忽略字段 | 首次分歧证据准确，不按 index 强配 |
| test_regression.py | 故障 fail/修复 pass、未调用退款、零断言、missing、inconclusive、stale baseline | 不产生任何已知假绿路径 |
| test_cli.py | 每个新命令、JSON stdout、旧参数、退出码、空 suite | 子进程端到端验证，而非只测 parser |
| test_server.py | token、Origin/Host、ID traversal、并发/重启/idempotency/cancel | 不执行任意 callable，job 不重复启动 |
| test_code_runner.py | 临时 Git 两 commit、不同依赖 marker、dirty/缺入口、超时清理 | 确实执行各自代码和环境；原工作区无变更 |
| test_release.py | wheel/source ZIP/vendor/静态 JS 一致 | 新 schema/模块全包含；无用户数据和旧 ZIP |
| tests/e2e/ | import failure→preflight→run→compare→case→suite；静态模式 | 真浏览器检查可见状态、键盘、错误、无控制台错误 |
| CI workflow | 故障版与修复版示例、JUnit/JSON、artifact 总生成 | 故障 job 非零，修复 job 0；不是比较静态成功文件 |

性能先测再设预算：在固定机器/profile 下记录无接入基线、buffered/sync 的吞吐和 p50/p95 capture 开销，100/1,000/2,000 步的导入/对齐时间与峰值内存。首个版本建议目标是 2,000 步比较 ≤2 秒、UI 首屏 ≤2 秒；这是验收目标，不是本次测得结果。生产接入开销由 design partner 的实际 workload 决定，不能用 demo 的无网络函数代表线上表现。

## 14. 分阶段任务与验收门

每阶段交付独立可审阅的改动；以下是实施阶段，不是当前授权修改代码。估算为单人专注工程时间，合计约 20–34 天，不含真实接入/供应商差异排查；两到三周只能优先完成首条闭环，不承诺完整平台。

### P0 · 固定兼容基线与构建来源（1–2 天）

文件：tests/test_sdk.py、tests/core.test.mjs、scripts/package_release.py、scripts/sync_plugin.py、pyproject.toml、插件脚本与 README、docs/migration.md。

- [ ] 固定 v1 contract fixtures 和现有 CLI/SDK smoke；检查发布构建来源。
- [ ] 增加 --check 与 vendor 同步，确认 dist 是前端唯一源。
- [ ] 写清命名/版本/确定性限制；暂不大拆 runtime。

验收：原 13 项测试通过；源码/SDK web/vendor 内容一致；ZIP 解包可执行离线 demo；修改任一生成物后 --check 能报漂移。无回归则进入 P1。

### P1 · v2 + 可恢复 failure capture（3–5 天）

文件：contracts、schema、migrations、serialization、redaction、storage、journal、provenance、bundle、capture，及 schema/ 与对应测试。

- [ ] v2 与 v1 内存迁移；SDK 新 input/set_output/fail。
- [ ] journal、durability、capture health、崩溃恢复。
- [ ] failure bundle export/import；代码/环境标识，脱敏缺口。
- [ ] CLI inspect/recover/bundle/doctor 的只读与预检部分。

验收：异常/业务违规都生成 failure；强杀恢复 interrupted；磁盘/脱敏错误不掩盖业务错误；v1 文件字节不变；bundle 校验与恶意路径测试通过。此阶段不宣传全流程确定性。

### P2 · 严格全流程重跑与可解释 diff（4–6 天）

文件：runtime/policy、matcher、fixtures、runner、worker、environment、fidelity，replay.py，comparison/，experiments.py 的单实验部分，project.py，cli.py。

- [ ] 注册入口、作用域匹配、精确 fixtures、异常映射、异步入口。
- [ ] 预检、超时/取消、无 fallback、真实 source_step 关联。
- [ ] 对齐/结构 diff；CLI run/compare；保留原 replay_step。
- [ ] 完整离线退款 demo：29900 → 2900；模拟写工具捕获参数。

验收：相同受控入口连续 20 次得到相同规范化执行摘要（排除 ID/timing）；这只证明 fixture workload，不推广为所有 agent。重复/并发/新参数测试没有一次触发未授权真实工具；diff 显示金额变化、scope 和 coverage，不说 fixture=恢复。

### P3 · 业务回归与 CI 闭环（3–5 天）

文件：regression/、case/result schemas、cli.py、examples/regression_demo、.github/workflows/agent-regression.yml、docs/ci.md。

- [ ] declaration assertions、create/test/accept、不可变 baseline。
- [ ] JSON/JUnit/HTML 报告、退出码、空 suite/无断言拦截。
- [ ] GitHub PR 离线 workflow；报告始终可下载。

验收：原故障代码 fail，修复代码 pass，再故意恢复 bug 后 CI 再次 fail；删除退款调用也 fail；改变 source/fixture/断言后 baseline stale；测试运行的是当前代码而不是 source JSON。**P3 结束才达到最小 Agent Regression Debugger 定位。**

### P4 · 跨原生模型、工具、代码实验（5–8 天）

文件：providers/、provider.py、capture.py 的 run.model、runtime/code_runner.py、experiments.py、project schema、CLI experiment/--commit、对应测试。

- [ ] 统一模型 IR + OpenAI-compatible；保留 run.chat 兼容。
- [ ] Anthropic 非流式 adapter；明确 capability errors。
- [ ] 注册工具替换、变量矩阵、重复次数和预算。
- [ ] 独立 worktree/环境、固定 engine、不同 ref 的真实执行证明。

验收：mock 跨协议多轮工具链通过；两个 Git commits 的实际行为不同并被 diff 捕获；changed branch 缺 fixture 返回 inconclusive；未经显式配置不进行 live call。真实双供应商 smoke 另行列为待验证或实测通过，不以 mock 替代声明。

### P5 · UI/API、发布与真实用例验证（4–8 天）

文件：server.py、dist 新模块和原页面、tests/e2e、插件 manifest/技能/README、README、docs、release workflows。

- [ ] jobs API、能力预检、failure→experiment→compare→case 主流程。
- [ ] 静态报告/静态 UI 与本地执行模式明确区分。
- [ ] 更新首页与插件定位/使用说明；保留命令名称。
- [ ] wheel/ZIP/plugin/静态站资源一致；浏览器主链路验证。

验收：不看命令行也能完成本机回归流程；服务器重启后结果可读；demo 明确 synthetic；从干净环境按 README 能运行离线用例；至少 3 个不同失败场景验证（业务违规、工具异常、分支 mismatch）。真实 design partner 记录不足时不宣称验证了 production readiness。

## 15. 可直接交给 Codex 的执行指令

下段在计划获准进入实施时使用；当前任务到计划交付为止。

```text
在 j112929/agent-replay-mvp 实施 Agent Regression Debugger 改造。
以本计划为验收规格，参考基线提交 e6a58768df22b6689cffffbf7214368ec4f98243。

先读取实际工作区的 AGENTS.md 和当前 Git 状态，将当前代码与计划基线比较；
保留用户未提交工作。若已有能力已经实现，以验收测试确认后复用，不重复实现。

按 P0 → P1 → P2 → P3 → P4 → P5 顺序推进，每阶段先做可运行的垂直切片。
保持 agent_replay 导入名、agent-replay CLI 和 v1 读取兼容。
不要把单步 recorded/fixture 的 success 当作业务恢复，也不要自动更新成功基线。
不从 trace/bundle 导入可执行入口，不在 mismatch 后调用真实工具。
每次 run 必须记录实际运行代码、环境、spec、fixtures 和保真度缺口。
dist 为前端源码；sdk/web 和插件 vendor 通过构建同步，禁止分别手改。

每阶段运行相关单元、契约、CLI 集成测试；阶段结束跑兼容基线和构建检查。
浏览器主流程到 P5 做完整 E2E；真实模型调用必须有已配置 provider 和明确运行范围，
无凭据时保留明确 skipped，不伪造 live 验证成功。

优先完成 P3 的完整离线闭环，再扩展原生模型/代码比较和完整 UI。
未经用户要求不发布、推送或部署。阶段报告列完成文件、验证结果、已知限制和下一阶段。
```

## 16. 交付时的产品验证

工程验证之后，用真实失败衡量：从收到 bundle 到首次有效重跑的时间、一次接入需要修改的边界数、无法重跑的原因分布、转为 case 的比例、CI 拦截到的再次退化、噪声/假失败比例。初期目标可以设为“受支持入口的首个失败在 10 分钟内完成本地重跑”，但应以实测结果调整。

不把竞争产品的功能推断写进实现验收；本计划仅用用户给定的产品方向与仓库实际代码制定。能证明产品价值的首个成果是：同一生产失败在旧代码下红、修复后绿、引入退化再红，并且每一步都能解释证据来自哪里。
