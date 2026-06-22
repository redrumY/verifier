# CoderWorkspaceRunner：隔离工作区、diff、sandbox 验证

这一步解决的是：Coder agent 不应该直接在原始 repo 里乱改代码。更合理的流程是先把项目复制到
隔离 workspace，让 Coder 在副本里工作，生成 diff，然后再用本地 verifier 或 Docker sandbox
验证。

当前实现：

```text
TaskPlan coder task
  -> TaskPack
  -> isolated workspace
  -> coder patch / coder tool loop
  -> git diff --no-index
  -> local Verifier or DockerSandboxRunner
  -> task.result
```

文件：

- `harness/coder_workspace_runner.py`
- `agents/s_full.py` 的 `run_coder_workspace_task` 工具

## 1. 为什么要有 isolated workspace

如果 Coder 直接在宿主 repo 改代码，会有几个问题：

- Coder 改坏了原项目，用户本地环境直接变脏。
- `npm install`、build、test 可能写入 `node_modules`、`dist`、`coverage`。
- 失败后很难知道哪些文件是 agent 改的，哪些是原项目已有变化。
- 多个 agent 并发时会互相覆盖。

所以先做一层轻量隔离：

```text
source project
  -> .workspaces/coder-runs/<run_id>/baseline
  -> .workspaces/coder-runs/<run_id>/workspace
```

`baseline` 是原项目快照，`workspace` 是 Coder 修改的副本。

两份副本都忽略：

```text
.git
node_modules
dist
build
coverage
.sandbox
.workspaces
.context
.tasks
outputs
```

这样 Coder 的修改不会污染原项目，也不会把依赖目录复制进去。

## 2. isolated workspace 和 Docker sandbox 的区别

这两个概念容易混：

| 名称 | 解决的问题 | 当前目录 |
| --- | --- | --- |
| isolated workspace | Coder 写代码不要污染源 repo | `.workspaces/coder-runs/<id>/workspace` |
| Docker sandbox | Verifier 跑命令/浏览器不要污染宿主环境 | `.workspaces/coder-runs/<id>/outputs/sandbox-runs/...` |

所以顺序是：

```text
先 workspace
  Coder 在副本里改代码

再 sandbox
  Verifier 把这个 workspace 再复制到 Docker run 目录里验证
```

面试讲法：

> Workspace isolation protects the user's source repo from code edits. Docker sandbox protects the host
> environment from install/build/test/browser verification. They are two different isolation layers.

## 3. TaskPack 怎么进入 Coder

`CoderWorkspaceRunner` 会从 `TaskPlan` 的 coder task 构造 `TaskPack`：

```text
task.title + task.description -> objective
task.acceptance_criteria     -> acceptance_criteria
task.context_refs            -> relevant_files
plan.metadata.constraints    -> constraints
```

然后写入：

```text
.workspaces/coder-runs/<id>/task-pack.json
.workspaces/coder-runs/<id>/task-prompt.txt
```

`TaskPack` 的作用不是执行代码，而是限制 Coder：

- 目标是什么
- 哪些文件最相关
- 验收标准是什么
- 哪些范围不能碰

## 4. Coder 怎么执行

当前支持两种方式。

### 方式 A：patch_text

工具调用可以传 `patch_text`，runner 会在 workspace 中执行：

```text
git apply coder.patch
```

这适合测试、eval replay、或者外部 agent 已经生成 patch 的场景。

### 方式 B：coder tool loop

`agents/s_full.py` 里新增了 `run_workspace_coder_tool_loop()`。如果调用
`run_coder_workspace_task` 时传：

```json
{
  "use_llm_coder": true
}
```

它会让 Coder agent 在 isolated workspace 中使用这些工具：

```text
read_file
write_file
edit_file
bash
```

这些工具的路径边界是：

```text
workspace_safe_path = workspace_dir / user_path
```

如果路径逃逸 workspace，会拒绝。

当前建议面试时这么讲：

> 现在已经把 Coder 执行抽象成 callback。生产环境里 callback 是 LLM tool loop；
> eval 里 callback 可以是 deterministic patch。这样同一个 workspace runner 既能服务真实 agent，
> 也能服务自动化评测。

## 5. diff 怎么生成

Runner 不依赖 source repo 的 `.git`，因为复制 workspace 时会忽略 `.git`。

它使用：

```sh
git diff --no-index -- baseline workspace
```

输出到：

```text
.workspaces/coder-runs/<id>/outputs/workspace.diff
```

这样即使目标项目不是 git repo，也能得到 diff。

优点：

- 不需要污染原项目 git 状态。
- 不需要创建 commit。
- 可以清楚看到 Coder 在 workspace 里改了哪些文件。

## 6. verification 怎么接 sandbox

`verification_mode` 有三个值：

```text
none
local
sandbox
```

### local

直接在 workspace 上跑 `Verifier`：

```text
Verifier(run_dir).verify("workspace")
```

这会执行项目类型相关的：

```text
npm install
npm run typecheck
npm run build
npm run test
browser verify
```

输出：

```text
.workspaces/coder-runs/<id>/outputs/local-verifier/verification-report.json
```

### sandbox

先准备 Docker sandbox：

```text
DockerSandboxRunner(run_dir).prepare_run("workspace")
```

如果 `run_sandbox=false`，只生成 Docker 运行目录，不启动 Docker。适合学习和面试展示。

如果 `run_sandbox=true`，再执行：

```text
docker compose run --rm verifier
```

输出：

```text
.workspaces/coder-runs/<id>/outputs/sandbox-runs/<id>_sandbox/
  project/
  sandbox/
  outputs/
  test-profile.json
  sandbox-run.json
```

## 7. task result 怎么回写

Runner 会更新原来的 coder task：

```text
status = completed / failed / in_progress
result = {
  "status": "...",
  "diff_path": ".../workspace.diff",
  "files_changed": [...],
  "verification": {...},
  "sandbox_run": {...}
}
context_refs += [diff_path, task_pack_path]
```

如果 `verification_mode=sandbox` 但 `run_sandbox=false`，状态会是：

```text
result.status = prepared
task.status = in_progress
```

因为 Docker 验证环境已经准备好，但还没有真正执行。

## 8. 现在如何调用

在 `agents/s_full.py` 工具里：

```json
{
  "task_id": "task_003",
  "plan_path": ".tasks/plans/dynamic_task_plan.json",
  "source_project_dir": ".",
  "verification_mode": "sandbox",
  "run_sandbox": false
}
```

如果要让真实 Coder LLM 在 workspace 里改：

```json
{
  "task_id": "task_003",
  "use_llm_coder": true,
  "verification_mode": "local"
}
```

如果 eval 已经生成 patch：

```json
{
  "task_id": "task_003",
  "patch_text": "diff --git ...",
  "verification_mode": "sandbox",
  "run_sandbox": true
}
```

## 9. 和 Hermes/Codex 风格的关系

Hermes/Codex 这类成熟系统一般会做：

```text
task board / kanban
  -> worker 领取 coder task
  -> 创建隔离 workspace / worktree
  -> worker 通过工具读写文件
  -> verifier 在 sandbox/CI 中验证
  -> diff/artifacts/status 回写 task board
```

当前项目是轻量版：

```text
JSON TaskPlan
  -> CoderWorkspaceRunner
  -> copied workspace
  -> local/sandbox verifier
  -> task.result
```

后续可以升级成：

- `git worktree` 替代 copy workspace
- SQLite Kanban 替代 JSON TaskPlan
- 真正的 worker pool 领取 task
- sandbox report 失败后自动生成 `FailureDigest`
- reviewer 读取 diff + verification report 决定是否合并

## 10. 面试总结

可以这样说：

> 我把 Coder 执行从“直接在 repo 里改代码”升级成了 workspace runner。Orchestrator
> 从 TaskPlan 里取 coder task，构造 TaskPack，然后复制项目到 isolated workspace。
> Coder 只在 workspace 中通过工具修改文件，修改后用 `git diff --no-index` 生成 patch。
> 接着 Verifier 可以在 workspace 本地验证，也可以把 workspace 交给 Docker sandbox 验证。
> 验证结果、diff 路径和变更文件会回写到 task.result。这样用户原项目不会被污染，同时每个
> agent task 都有可审计的 diff 和 verification artifact。
