# 第 10 步：工程化效果验证设计

这份文档解释：当 ModelGateway、ContextManager、TaskPlanner、FrontendGenerator、
Verifier、DockerSandboxRunner、FailureFeedback 这些设计都有了之后，如何用工程手段证明
coding agent 真的变强了，而不是只是在简历上写了更多模块名。

核心结论：

> 不能只说“我优化了架构”。要用固定任务集、可复现运行环境、失败注入、基准对比和指标报告证明：
> 成功率提高了、失败能被定位了、修复闭环能收敛、宿主项目没有被污染、成本和上下文可控。

## 1. 先纠正一个认知：不是“所有内容都做完了”

现在项目已经有了比较完整的骨架：

- `ModelGateway`：统一模型调用、角色策略、预算、重试、成本记录
- `ContextManager`：短期上下文、工作记忆、归档和压缩
- `TaskPlanner`：把自然语言变成 task + acceptance criteria
- `FrontendGenerator`：生成完整 Vite/React/TS 工程，而不是只吐一个组件
- `Verifier`：安装、typecheck、build、test、browser 检查
- `DockerSandboxRunner`：用隔离目录和 Docker profile 运行验证
- `FailureFeedback`：已有详细设计，下一步实现 digest/classifier

但从工程角度看，这还不是“产品完成”，而是进入下一阶段：

```text
从功能建设阶段
  -> 进入效果验证阶段
```

面试里不要说：

> 我把所有内容都做完了。

更好的说法是：

> 我把 coding agent 的主链路拆成几个可测试模块，并补了工程化验证方案。
> 接下来不是继续堆 agent，而是用固定 benchmark 和 failure injection 验证每个模块是否真的提升成功率。

## 2. 验证目标

工程化验证要回答 6 个问题：

- 代码是否能跑：unit/integration tests 是否通过
- 功能是否有效：自然语言到前端工程是否能完整交付
- 失败是否可定位：build/browser/API 失败是否能生成结构化 digest
- 修复是否能收敛：最多 3 次 retry 内是否能修好
- 环境是否隔离：原项目是否没有被 `node_modules/dist/lockfile` 污染
- 成本是否可控：token、模型调用次数、耗时是否可记录

不要只看一个指标。coding agent 是系统工程，至少要有：

```text
Correctness + Reproducibility + Isolation + Cost + Repairability
```

## 3. 指标 Scorecard

每次 evaluation 输出一个 scorecard：

```json
{
  "run_id": "eval_20260622_001",
  "git_sha": "abe8671",
  "task_set": "frontend_ui_v1",
  "summary": {
    "total_tasks": 10,
    "passed": 7,
    "failed": 3,
    "pass_rate": 0.7,
    "avg_attempts": 1.8,
    "avg_duration_ms": 92134,
    "host_dirty_after_run": false
  },
  "quality": {
    "minimum_delivery_rate": 1.0,
    "build_pass_rate": 0.8,
    "browser_pass_rate": 0.7,
    "console_error_free_rate": 0.7,
    "api_contract_pass_rate": 0.6
  },
  "repair": {
    "first_try_pass_rate": 0.4,
    "fixed_after_retry_rate": 0.5,
    "max_attempt_stop_count": 2
  },
  "cost": {
    "model_calls": 31,
    "input_tokens": 86000,
    "output_tokens": 17000,
    "estimated_cost_usd": 1.42
  },
  "artifacts": {
    "report": "data/evaluation/results/eval_20260622_001.json",
    "screenshots_dir": "data/evaluation/artifacts/eval_20260622_001/screenshots"
  }
}
```

面试里这个对象很重要。它表示你不是靠感觉判断 agent 好不好，而是把每次运行变成可比较的数据。

## 4. 验证分层

推荐 5 层验证，从便宜到昂贵：

```text
T0: Unit tests
T1: Contract/schema tests
T2: Fixture integration tests
T3: Docker sandbox smoke tests
T4: Replay benchmark / live LLM eval
```

### T0：Unit tests

目标：

- 模块内部逻辑正确
- 不依赖真实 LLM
- 不依赖 Docker
- 运行快

当前已有：

```sh
python3 -m pytest -q
```

覆盖：

- `ModelGateway` 角色策略、预算、重试
- `ContextManager` 上下文压缩、归档
- `TaskPlanner` 任务拆分
- `FrontendGenerator` 最低交付文件
- `Verifier` install/build/browser 报告
- `DockerSandboxRunner` prepare/run manifest

面试讲法：

> 第一层我先保证每个模块都是可测的，避免 coding agent 变成一个不可测试的大 while loop。

### T1：Contract / schema tests

目标：

- 检查模块之间传递的数据结构是否稳定
- 让 agent 产物可以被机器消费

应该验证这些 schema：

- `TaskPlan`
- `Task`
- `FrontendProjectSpec`
- `VerificationReport`
- `SandboxRun`
- `SandboxExecutionResult`
- `FailureDigest`
- `SessionSummary`
- `ModelCallRecord`

例如：

```text
TaskPlanner 输出的 task.context_refs
  -> ContextManager 能识别

DockerSandboxRunner 输出 sandbox-run.json
  -> run() 能重新 load

Verifier 输出 verification-report.json
  -> FailureClassifier 能生成 digest
```

面试讲法：

> 多 agent 系统最大的问题不是模型会不会说话，而是中间 artifact 能不能稳定传递。
> 所以我会把每个 artifact 当成 contract 来测。

### T2：Fixture integration tests

目标：

- 不调用真实 LLM
- 用固定输入模拟 agent 产物
- 验证完整链路是否能跑

建议准备一组 fixture：

```text
fixtures/
  frontend_basic/
    input_request.txt
    expected_files.json

  ui_component_success/
    base_project/
    patch.diff
    expected_report.json

  ui_component_typescript_error/
    base_project/
    patch.diff
    expected_failure_digest.json

  ui_api_contract_mismatch/
    base_project/
    patch.diff
    api_contract.json
    mock_responses.json
    expected_failure_digest.json
```

这些 fixture 不追求大而全，只追求能证明关键链路。

### T3：Docker sandbox smoke tests

目标：

- 证明验证确实在隔离环境中运行
- 证明原项目不会被污染
- 证明 browser screenshot / console errors 能采集

最低 smoke test：

```text
1. 准备一个最小 Vite React fixture
2. 调用 DockerSandboxRunner.prepare_run()
3. 调用 DockerSandboxRunner.run()
4. 检查 outputs/verification-report.json
5. 检查原项目没有新增 node_modules/dist
6. 检查 git status 没有意外变化
```

隔离验证要特别检查：

- source project 没有 `node_modules`
- source project 没有 `dist`
- source project 没有 `coverage`
- source project 没有被 patch 直接改写
- `.sandbox/runs/<id>` 有完整 artifacts

面试讲法：

> 我不只是说用了 Docker，我会验证 Docker 运行之后宿主项目是否仍然干净。
> 对 coding agent 来说，隔离不是概念，而是要有 dirty check。

### T4：Replay benchmark / live LLM eval

目标：

- 用固定任务集比较优化前后效果
- 让结果可复现
- 量化成功率和成本

任务集示例：

```json
[
  {
    "id": "fe_001",
    "type": "generate_frontend_project",
    "prompt": "生成一个响应式 CRM 客户列表页面",
    "acceptance": [
      "package.json exists",
      "npm run build passes",
      "page opens without console errors"
    ]
  },
  {
    "id": "fe_002",
    "type": "add_ui_component",
    "prompt": "在现有项目里新增一个 UserCard 组件并接入页面",
    "acceptance": [
      "component exported",
      "build passes",
      "browser screenshot captured"
    ]
  },
  {
    "id": "fe_003",
    "type": "ui_api_integration",
    "prompt": "新增用户列表组件，调用 /api/users 并处理 loading/empty/error",
    "acceptance": [
      "mock API success state renders",
      "empty state renders",
      "error state renders",
      "no console errors"
    ]
  }
]
```

对每个任务跑两种模式：

```text
baseline:
  单 agent / 直接生成组件 / 无 sandbox / 无 digest

optimized:
  ModelGateway + ContextManager + TaskPlanner + full project + Verifier + Sandbox + FailureDigest
```

比较：

- pass rate
- first try pass rate
- retry success rate
- average attempts
- token cost
- wall clock duration
- dirty workspace count
- failure digest accuracy

## 5. 怎么验证每个优化真的有效

### 5.1 ModelGateway

验证点：

- 不同 role 使用不同 model policy
- 并发限制生效
- token budget 超限会拒绝
- 失败会 retry/backoff
- 每次调用都有 cost record

工程方法：

- fake provider 测试
- 并发调用测试
- budget 边界测试
- retry 次数断言
- call log snapshot

效果指标：

- `model_calls_recorded_rate = 100%`
- `budget_violation_count = 0`
- `unexpected_direct_api_key_usage = 0`

### 5.2 ContextManager

验证点：

- 子 agent 不继承父 agent 全量上下文
- transcript 能归档
- summary 能回填
- tool result 长度能截断
- compact 后仍保留关键决策

工程方法：

- 构造长 messages
- 触发 compact
- 检查 summary 字段
- 检查 transcript 文件存在

效果指标：

- `context_size_reduction_ratio`
- `required_fields_preserved_rate`
- `tool_result_truncation_count`

### 5.3 TaskPlanner

验证点：

- 自然语言能拆出 task
- 每个 task 有 owner/status/depends_on/context_refs
- acceptance criteria 可验证

工程方法：

- 固定 prompt snapshot
- task schema test
- dependency order test

效果指标：

- `task_schema_valid_rate`
- `acceptance_criteria_coverage`

### 5.4 FrontendGenerator

验证点：

- 不是只生成 React component
- 有完整工程文件
- package/scripts/dependencies 可安装构建

工程方法：

- minimum delivery test
- package.json script test
- generated file snapshot

效果指标：

- `minimum_delivery_rate`
- `build_script_presence_rate`

### 5.5 Verifier

验证点：

- 能检测项目类型
- 能按项目类型选择命令
- build 失败时跳过 browser
- browser console error 能导致失败

工程方法：

- fake runner 注入成功/失败
- fake browser runner 注入 console error
- report schema test

效果指标：

- `failure_detection_rate`
- `false_pass_count`

### 5.6 DockerSandboxRunner

验证点：

- prepare 阶段复制项目但忽略污染目录
- run 阶段用 docker compose 执行
- outputs 写到 sandbox run 目录
- 原项目不被写入

工程方法：

- fixture project
- fake docker runner
- real docker smoke test
- source dirty check

效果指标：

- `host_dirty_after_run = false`
- `sandbox_artifact_completeness`

### 5.7 FailureFeedback

验证点：

- 能从 logs 生成 digest
- 能区分 TypeScript/build/browser/API 错误
- digest 短小但包含关键证据
- retry 决策不把 infra error 交给 Coder

工程方法：

- 故障日志 fixture
- expected digest snapshot
- classifier rule tests
- retry policy tests

效果指标：

- `digest_accuracy`
- `avg_digest_tokens`
- `retry_decision_accuracy`

## 6. Failure injection：故意制造失败

只测成功路径不够。coding agent 的核心价值是失败后能修。

建议至少准备这些故障：

### TypeScript error

注入：

```tsx
<Button />
```

但 `ButtonProps` 要求：

```tsx
onClick: () => void
```

期望：

```json
{
  "error_type": "typescript_error",
  "failed_step": "build",
  "affected_files": ["src/App.tsx"]
}
```

### Browser runtime error

注入：

```ts
throw new Error("boom")
```

期望：

```json
{
  "error_type": "browser_console_error",
  "failed_step": "browser_check"
}
```

### API connection error

注入：

```text
VITE_API_BASE_URL=http://127.0.0.1:9999
```

期望：

```json
{
  "error_type": "api_connection_error",
  "retryable": false,
  "suggested_next_action": "switch to mock profile or check backend availability"
}
```

### API contract mismatch

contract：

```json
{ "required": ["name"] }
```

UI 读取：

```ts
user.fullName
```

期望：

```json
{
  "error_type": "api_contract_mismatch",
  "affected_files": ["src/components/UserList.tsx"]
}
```

### Sandbox infra error

注入：

```text
docker image not available
```

期望：

```json
{
  "error_type": "sandbox_infra_error",
  "retryable": false,
  "owner": "operator"
}
```

这类失败不应该让 Coder 修改业务代码。

## 7. 对比实验怎么做

推荐每次评估使用同一套任务：

```text
data/evaluation/tasks/frontend_ui_v1.json
```

跑两遍：

```text
baseline run
optimized run
```

baseline 可以是：

- 原始项目能力
- 单 agent 直接生成组件
- 不做 sandbox
- 不做 context compression
- 不做 failure digest

optimized 是当前设计：

- full project generation
- verifier
- sandbox
- structured failure feedback
- retry loop

最终报告：

```text
Baseline:
  pass_rate: 30%
  avg_attempts: 1.0
  dirty_workspace: 4/10
  failure_diagnosed: 0/7

Optimized:
  pass_rate: 70%
  avg_attempts: 1.8
  dirty_workspace: 0/10
  failure_diagnosed: 6/7
```

面试里这比讲架构图有杀伤力。

## 8. 一次完整 evaluation 的执行步骤

### Step 1：记录基线环境

```sh
git rev-parse HEAD
python3 --version
node --version
npm --version
docker --version
```

记录到：

```text
data/evaluation/results/<run_id>/environment.json
```

### Step 2：跑单元测试

```sh
python3 -m pytest -q
```

输出：

```text
unit_tests: passed
```

### Step 3：跑 fixture integration

用固定 fixture 验证：

- generator 输出完整工程
- verifier 能生成 report
- sandbox prepare 能生成 manifest
- failure classifier 能生成 digest

### Step 4：跑 Docker smoke

只跑 1 到 2 个小任务，避免太慢。

检查：

- sandbox report 存在
- screenshot 存在
- 原项目无污染

### Step 5：跑 replay benchmark

对固定任务集执行：

```text
task -> plan -> code -> verify -> digest -> retry -> final report
```

### Step 6：生成 scorecard

输出：

```text
data/evaluation/results/<run_id>/scorecard.json
data/evaluation/results/<run_id>/summary.md
```

## 9. 项目里建议新增的目录

下一步可以加：

```text
data/
  evaluation/
    tasks/
      frontend_ui_v1.json
    fixtures/
      frontend_basic/
      ui_component_success/
      ui_component_typescript_error/
      ui_api_contract_mismatch/
    results/

eval/
  run_eval.py
  scorecard.py
  replay_runner.py
```

注意：

- `fixtures/` 可以进 Git
- `results/` 通常不进 Git，只保留示例报告
- 大截图、trace、node_modules 不进 Git

## 10. 面试回答模板

可以这样说：

> 我不会只用 demo 来证明 coding agent 有效，因为 demo 很容易挑简单任务。
> 我的验证方案分五层：unit test 验模块，contract test 验 artifact schema，
> fixture integration 验固定链路，Docker smoke 验隔离环境，最后用 replay benchmark
> 对比 baseline 和 optimized 的 pass rate、retry success、dirty workspace、token cost。

继续讲：

> 例如 UI + API 联调，我会准备 contract mismatch、API connection error、browser runtime error
> 这些 failure injection case。验证目标不是所有任务一次成功，而是失败后能不能生成结构化 digest，
> Coder 能不能在 3 次以内修好，以及 infra error 会不会被错误地交给 Coder。

最后落到结果：

> 所以我的指标不是“模型说它完成了”，而是 `npm run build`、browser check、console error、
> screenshot、API contract、sandbox dirty check 和 scorecard。这样面试官可以看到系统是否真的可用。

## 11. 现在这个项目马上能跑什么

当前已经能跑：

```sh
python3 -m pytest -q
```

这证明：

- 现有模块单元测试通过
- sandbox prepare/run 的假 Docker 路径可测
- verifier 的失败分支可测

当前还不能完整证明：

- 真实 Docker 镜像可拉取并运行
- 真实浏览器截图质量
- 真实 LLM 生成代码后的成功率
- FailureDigest 分类准确率
- retry loop 收敛率

所以后续工程验证应该优先实现：

```text
1. failure_feedback.py
2. workflow_state.py
3. data/evaluation/tasks/frontend_ui_v1.json
4. eval/run_eval.py
5. 一个真实 Docker smoke fixture
```

## 12. 最小可交付评估版本

如果时间只有半天，最小版本做这个：

- 5 个固定前端任务
- 3 个故障注入 case
- 一个 `eval/run_eval.py`
- 输出一个 `scorecard.json`
- README 放一段评估结果

最小指标：

```json
{
  "unit_tests": "passed",
  "fixture_tasks": {
    "total": 5,
    "passed": 4
  },
  "failure_injection": {
    "total": 3,
    "classified": 3
  },
  "sandbox_isolation": {
    "host_dirty_after_run": false
  }
}
```

这已经足够支撑面试里的工程性回答。
