# Verifier

Verifier 正在从 Claude-Code-style 教程仓库改造成一个
verification-first coding agent harness。

当前目标先收窄：只做前端项目生成。Agent 不以“生成 React 组件文本”为交付物，而是生成完整前端工程，安装、构建、启动、浏览器验证，并把失败日志反馈给 agent 修复。

## 当前运行结构

更详细的数据流和面试讲法见：

- [ModelGateway + ContextManager 数据流](docs/model-gateway-context-flow-zh.md)
- [第 3 步：上下文压缩设计](docs/context-compression-zh.md)
- [第 4 步：任务拆分设计](docs/task-planning-zh.md)
- [第 4.5 步：动态任务规划设计](docs/dynamic-task-planning-zh.md)
- [第 5 步：生成完整前端工程设计](docs/frontend-generation-zh.md)
- [第 6 步：验证代码质量设计](docs/verifier-zh.md)
- [第 7 步：Docker Sandbox Runner 设计](docs/docker-sandbox-runner-zh.md)
- [第 8 步：受控 Agent Workflow 状态机设计](docs/agent-workflow-state-machine-zh.md)
- [第 9 步：失败反馈闭环设计](docs/failure-feedback-loop-zh.md)
- [第 10 步：工程化效果验证设计](docs/evaluation-harness-zh.md)
- [DeepSeek v4 Pro 接入 ModelGateway 设计](docs/deepseek-gateway-zh.md)

```text
用户需求
  -> agents/s_full.py
  -> harness.ModelGateway
  -> 按角色调用不同 agent
  -> harness.ContextManager
  -> 工具 / task board / subagent
  -> 后续 verifier + sandbox report
```

根目录下旧的 `s01_*` 到 `s20_*` 教程章节已经删除。当前主入口是：

```sh
python agents/s_full.py
```

如果要使用 DeepSeek v4 Pro，本地复制配置模板：

```sh
cp .env.example .env
```

然后只在本机 `.env` 里填写真实 `DEEPSEEK_API_KEY`。`.env` 已经被 `.gitignore`
忽略，不要把真实 key 提交到 GitHub。

启动 DeepSeek agent：

```sh
bash scripts/run_deepseek_agent.sh
```

## 为什么 gateway 要配合 full.py

`agents/s_full.py` 是现在最接近“完整 agent loop”的入口，里面已经有：

- tool dispatch
- Todo / task
- subagent
- background task
- team / inbox
- context compact

但是它原来直接创建 provider client，并在多个位置调用 `client.messages.create(...)`。这不适合多 agent，因为每个 agent 会各管各的 API、预算、重试和日志。

现在改成：

```text
full.py 只负责 orchestration
ModelGateway 负责所有模型调用
```

也就是说，后续 Planner / Coder / Tester / Reviewer / Summarizer 都不直接拿 API key，只通过：

```python
call_model("coder", messages, system=..., tools=...)
```

进入统一网关。

## ModelGateway 能力

`harness/model_gateway.py` 负责：

- 统一读取 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY`
- 根据 agent role 分配模型
- 控制并发
- 控制 token budget
- 记录调用成本、token、latency、状态
- 对 429 / 529 / timeout 做 retry 和 backoff

默认角色策略：

| Role | 用途 |
| --- | --- |
| `planner` | 拆任务和主控规划 |
| `coder` | 写代码 |
| `tester` | 设计测试和分析验证结果 |
| `reviewer` | 看 diff、风险和缺失测试 |
| `summarizer` | 压缩上下文 |
| `verifier` | 不走 LLM，执行确定性检查 |

常用环境变量：

```sh
ANTHROPIC_API_KEY=...
DEEPSEEK_API_KEY=...
MODEL_ID=...
STRONG_MODEL_ID=...
MEDIUM_MODEL_ID=...
CHEAP_MODEL_ID=...
MODEL_GATEWAY_MAX_CONCURRENT=4
MODEL_GATEWAY_TOKEN_BUDGET=200000
```

DeepSeek v4 Pro 推荐配置：

```sh
MODEL_PROVIDER=deepseek
DEEPSEEK_MODEL_ID=deepseek-v4-pro
DEEPSEEK_CHEAP_MODEL_ID=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_THINKING=enabled
DEEPSEEK_REASONING_EFFORT=high
```

也可以按角色覆盖，例如：

```sh
CODER_MODEL_ID=...
TESTER_TOKEN_BUDGET=50000
```

## ContextManager 能力

`harness/context_manager.py` 解决的是多 agent 的上下文边界问题：
不要让子 agent 继承父 agent 的全部聊天记录，也不要把子 agent 的完整
中间过程塞回父 agent。

现在的策略是：

- 父 agent 为子 agent 创建 `TaskPack`
- `TaskPack` 只包含目标、验收标准、相关文件、上下文引用和约束
- 子 agent 在独立 session 里工作
- 子 agent 的完整 transcript 归档到 `.context/transcripts/`
- 子 agent 完成后只把 `SessionSummary` 回传给父 agent
- 长 tool result 会被截断，避免 prompt 被日志撑爆
- session 可以 compact：保留摘要和最近消息

这套机制对应面试里的回答就是：

```text
API 分配由 ModelGateway 做
上下文隔离由 ContextManager 做
父子 agent 之间传 task pack 和 summary，不共享完整上下文
```

## 后续扩展路线

不要继续把所有东西塞进 `agents/s_full.py`。后续模块应该放进 `harness/`：

```text
harness/
  model_gateway.py       # 已完成
  context_manager.py     # 已完成
  task_planner.py        # 已完成
  frontend_generator.py  # 已完成
  verifier.py            # 已完成
  sandbox_runner.py      # 已完成
  workflow_state.py      # 下一步：受控状态机
  failure_feedback.py    # 已完成：失败日志结构化和修复闭环基础分类器
eval/
  run_eval.py            # 已完成：固定任务集 replay 和 scorecard
  probe_external_project.py # 已完成：外部 GitHub 项目任务包和 sandbox 探针
```

`agents/s_full.py` 保持为主控入口；复杂运行时能力逐步拆到 `harness/`。

## 验证

```sh
python -m pytest -q
```

运行最小评估：

```sh
python eval/run_eval.py --run-id eval_smoke
```

探测外部前后端项目：

```sh
python eval/probe_external_project.py /path/to/fullstack-project --frontend-dir frontend
```
