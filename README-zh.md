# Verifier

Verifier 正在从 Claude-Code-style 教程仓库改造成一个
verification-first coding agent harness。

当前目标先收窄：只做前端项目生成。Agent 不以“生成 React 组件文本”为交付物，而是生成完整前端工程，安装、构建、启动、浏览器验证，并把失败日志反馈给 agent 修复。

## 当前运行结构

```text
用户需求
  -> agents/s_full.py
  -> harness.ModelGateway
  -> 按角色调用不同 agent
  -> 工具 / task board / subagent
  -> 后续 verifier + sandbox report
```

根目录下旧的 `s01_*` 到 `s20_*` 教程章节已经删除。当前主入口是：

```sh
python agents/s_full.py
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

- 统一读取 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`
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
MODEL_ID=...
STRONG_MODEL_ID=...
MEDIUM_MODEL_ID=...
CHEAP_MODEL_ID=...
MODEL_GATEWAY_MAX_CONCURRENT=4
MODEL_GATEWAY_TOKEN_BUDGET=200000
```

也可以按角色覆盖，例如：

```sh
CODER_MODEL_ID=...
TESTER_TOKEN_BUDGET=50000
```

## 后续扩展路线

不要继续把所有东西塞进 `agents/s_full.py`。后续模块应该放进 `harness/`：

```text
harness/
  model_gateway.py       # 已完成
  context_manager.py     # task pack、summary、transcript archive
  frontend_generator.py  # 自然语言 -> Vite/React 工程
  sandbox_runner.py      # 独立目录、命令白名单、timeout
  verifier.py            # build/typecheck/Playwright/report
```

`agents/s_full.py` 保持为主控入口；复杂运行时能力逐步拆到 `harness/`。

## 验证

```sh
python -m pytest -q
```
