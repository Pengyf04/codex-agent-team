# Codex Agent Team

[English](README.md)

Codex Agent Team 是一个面向 Codex 的多智能体编排插件，通过四个职责隔离的真实 subagent 完成复杂任务：

```text
Planner ↔ Plan Reviewer → 人工卡点 A
                         ↓
Executor ↔ Verifier → Planner 最终验收 → 人工卡点 B
```

工作流把状态和交接材料持久化到文件，限制自动循环次数，立即上报风险，并能在对话中断或上下文压缩后从记录状态恢复，而不是依赖主对话记忆。

## 核心能力

- 使用真实 Codex subagent，而不是在单个 Agent 中模拟角色。
- Planner、对抗式 Plan Reviewer、唯一写入者 Executor、独立 Verifier。
- 每个周期规划评审最多 5 轮、完整验证最多 8 轮。
- 修改产品文件前和最终完成前各有一个人工确认卡点。
- 使用结构化结论标记驱动分支。
- 零第三方依赖的 Python 状态机，原子写入 JSON，并持久化幂等请求记录。
- `.agent-team/runs/` 文件交接和中断恢复。
- 关键附加测试必须先固化为标准命令可达的正式回归测试，才能支撑 `PASS` 或关闭风险。
- 风险具有稳定 ID、`open → mitigated → closed` 生命周期和不可丢失的事件历史。
- 分别统计 Executor 总派发、验证退回修复和单次执行内的自检修复轮次。
- `DONE` 持久化已完成范围、明确排除范围、部署状态、开放风险和最终证据摘要。
- 有意收敛为两种交付模式：真实工作区或 run 内暂存交付物。

第一版不要求用户安装 `.codex/agents/*.toml`，也不引入 MCP Server。四个角色定义随 Skill 分发，subagent 默认继承当前 Codex 的模型、推理强度和权限模式。

## 环境要求

- 支持 Plugins 和 subagent 的较新 Codex 版本。
- Python 3.10 或更高版本，用于运行状态脚本。
- Git 非强制；`workspace` 模式建议使用。

| 平台 | v0.1.0 支持状态 |
|---|---|
| macOS | 正式支持 |
| Linux | 正式支持 |
| Windows | 实验性；CI 覆盖基础行为，但仍需在具体目标环境验证 Marketplace 安装 |

## 从 GitHub 安装

把公开仓库添加为 Marketplace，再安装 Plugin：

```bash
codex plugin marketplace add Pengyf04/codex-agent-team
codex plugin add codex-agent-team@codex-agent-team
```

安装后新建一个 Codex 任务，让新 Skill 被加载。

## 本地开发安装

克隆仓库后，把仓库根目录作为本地 Marketplace 添加：

```bash
codex plugin marketplace add /absolute/path/to/codex-agent-team
codex plugin add codex-agent-team@codex-agent-team
```

Marketplace 位于 `.agents/plugins/marketplace.json`，真正可安装的插件位于 `plugins/codex-agent-team/`。

## 更新与卸载

刷新 Git Marketplace 快照、重新安装 Plugin，然后新建任务：

```bash
codex plugin marketplace upgrade codex-agent-team
codex plugin add codex-agent-team@codex-agent-team
```

先卸载 Plugin，再移除 Marketplace 来源：

```bash
codex plugin remove codex-agent-team@codex-agent-team
codex plugin marketplace remove codex-agent-team
```

卸载 Plugin 不会删除目标工作区中的 `.agent-team/` 运行记录；不再需要时应单独检查并清理。

## 使用方法

从 Codex 的 Skill/Plugin mention 选择器中显式调用：

```text
$agent-team 给订单更新流程增加乐观锁，保持现有 API 行为并补充回归测试。
```

在 ChatGPT 桌面应用中，也可以输入 `@`，然后从已安装插件中选择 **Agent Team**；在交互式 Codex CLI 中，输入 `$` 后从自动补全中选择 `agent-team`。特别是本机安装了大量 Skill 时，从选择器选中比通过非交互包装器传入同样的普通文本更可靠。

普通任务不会自动触发这个 Skill。如果缺少可判定的验收标准，或者交付位置涉及实质选择，主控会用 `[PRE-INIT ACCEPTANCE CONFIRMATION]` 标记并在启动前向用户确认；这是初始化前置条件，不属于人工卡点 A 或 B。

如果本机安装了大量 Skill，可选的[失败关闭调用守卫](docs/explicit-invocation-guard.md)能在 canonical Skill 未暴露时阻止显式 Agent Team 请求静默降级为普通流程。该守卫只负责检测并停止，不会修改 Codex 宿主，也不能强制加载缺失的 Skill。

### 人工卡点 A

Planner 和 Plan Reviewer 达成一致后，Codex 会展示：

- 定稿方案和验收标准。
- 交付模式、请求路径及 canonical 绝对输出路径。
- 预计修改范围。
- 初始化时已有的 Git 改动和已知风险。

用户明确批准前不会启动 Executor。

### 人工卡点 B

Verifier 通过且 Planner 完成独立终验后，Codex 会展示：

- canonical 绝对输出路径，以及交付物入口和实际改动文件的绝对路径。
- 验证命令、检查项和证据。
- Executor 总派发、正式回修和内部自修轮次。
- 最终验收结论。
- 全部已记录风险及当前状态；没有则明确写“无”。
- 已通过状态机预检、即将写入 `DONE` 的完成事项、明确排除事项和部署状态，以及范围哈希和含义清晰的证据哈希。

在展示人工卡点 B 前，Codex 会先通过状态机的 `prepare_final` 严格预检 `completion_scope`；拼写错误或未知字段会在请求用户批准前被拒绝。只有用户确认这个已暂存的精确范围后，运行状态才会变为 `DONE`；最终确认直接消费暂存值，不再重新构造范围。

## 交付模式

| 模式 | 适用任务 | 产品输出位置 |
|---|---|---|
| `workspace` | 修改现有项目 | 真实目标工作区 |
| `staged` | 生成独立产物 | 本次运行的 `deliverable/` |

只有 Executor 可以有意修改产品文件。Planner、Plan Reviewer 和 Verifier 对产品文件保持只读；Verifier 运行测试时可以产生正常的临时构建或测试文件。这些是工作流规则，并非独立的操作系统沙箱：所有 subagent 继承主任务权限，因此状态机还会验证新鲜角色证据，并要求 Verifier 检测实际写入越界。

V1 明确拒绝 `user_path`、`worktree` 和非空 `output_path`；`staged` 产物必须自包含，不能含符号链接。这样可以把人工卡点绑定和故障恢复限制在可稳定审计的范围内。

## 运行记录

每次运行会创建：

```text
.agent-team/runs/<timestamp>-<slug>/
├── state.json
├── task.md
├── plan.md
├── review-log.md
├── verify-report.md
├── final-review.md
├── deliverables.md
├── risk-log.md
├── .state.lock        # 状态机专用并发锁
└── deliverable/        # 仅 staged 模式
```

主控通过受保护的 `.agent-team/request.json` 占位文件执行每次状态操作。`state.py guard` 会收紧本地权限、创建或复用占位文件并返回新的 `request_id`；包括 `show` 在内的每个成功操作都会在 `state.json` 中把该 ID 绑定到完整 JSON payload 摘要，然后删除请求文件。因此只有完全相同的保留请求才是幂等重试，不同 payload 不能复用该 ID。任务文本、反馈、风险和 Marker detail 都由文件编辑工具写入 JSON，不会拼接进 Shell 命令。Verifier 的结构化 `test_evidence` 会阻止关键临时测试直接支撑 `PASS`：状态进入 `NEEDS_TEST_PERSISTENCE`，直到 Executor 固化测试且 Verifier 通过标准命令复验同一证据 ID。风险通过 `risk_update` 缓解、关闭或重新打开，原始风险和全部事件始终保留。状态机还保存 Executor 的三层轮次统计，输出 canonical 绝对路径和含义明确的证据哈希，并在最终批准前预检结构化完成范围。卡点 A 批准的计划贯穿执行阶段，`PASS` 冻结交付内容、`deliverables.md` 和 `verify-report.md`，卡点 B 继续绑定终验与风险材料；`DONE` 是终态，不会因后续材料漂移而被重新激活。

必须在目标项目的忽略规则中加入 `.agent-team/`。Skill 发现它未被忽略时会提醒，发现已经被 Git 跟踪时会拒绝运行，但不会自行修改 `.gitignore`。运行记录默认保留，可能包含任务文本、证据、风险、暂存交付物和本机绝对路径；处理敏感任务前请阅读[安全与隐私说明](docs/security-and-privacy.md)。

## 安全与成本

- subagent 继承主 Codex 任务的权限模式。
- 角色隔离是行为层的质量控制，不是进程间安全边界。
- 未经明确授权，不会提交、推送、创建远程、合并或删除分支。
- 必须保留用户已有的未提交改动。
- 多智能体工作流比单 Agent 显著消耗更多时间和 Token，建议只用于真正复杂的任务；硬性循环上限用于避免无限重试。
- `BLOCKED` 状态只有在用户解决阻塞或批准恢复路径后才能继续。
- 阻塞运行只能恢复到记录的原阶段；评审后改变交付模式会强制重新规划。
- V1 每个 workspace 只允许一个未完成 run 和一个协作式主控。
- V1 不承诺抵御本地恶意伪造 `state.json`、并发请求写入、跨进程严格 exactly-once、操作系统级角色隔离，也不恢复旧开发 schema 的 run。
- Plugin 本身不新增遥测、账号登录或 API Key 要求；父 Codex 任务使用的其他工具仍受各自权限和网络行为约束。

## 故障排查

可用 `codex plugin list --available --json` 检查 Marketplace 发现和安装状态。如果 Codex 提示 Skill 目录超过上下文预算，请新建任务，并从 mention 选择器中明确选择 **Agent Team** 或 `agent-team`。在安装了大量 Skill 的环境中，原样写进 `codex exec` 提示词的 `$agent-team` 普通文本，不能可靠替代结构化的 Skill 选择动作。

## 开发与校验

运行仓库校验和状态机测试：

```bash
python3 scripts/validate.py
python3 -m unittest discover -s tests -v
python3 scripts/smoke_local_install.py
python3 scripts/smoke_local_install.py \
  --marketplace-source Pengyf04/codex-agent-team \
  --exercise-upgrade
```

CI 会在 Ubuntu Python 3.10/3.12/3.14 以及 macOS、Windows Python 3.12 上运行回归测试，并额外执行固定版本的 OpenAI Skill/Plugin 校验器、Ruff、Mypy strict、Bandit、CodeQL、Pull Request 依赖审查、包清单和秘密/PII 检查。开发依赖固定在 [requirements-dev.txt](requirements-dev.txt)。

已批准的实施设计保存在 [plan.md](plan.md)。

架构、可选调用守卫、贡献、发布和漏洞报告细节分别见 [docs/architecture.md](docs/architecture.md)、[docs/explicit-invocation-guard.md](docs/explicit-invocation-guard.md)、[CONTRIBUTING.md](CONTRIBUTING.md)、[docs/development-and-release.md](docs/development-and-release.md) 与 [SECURITY.md](SECURITY.md)。

## 来源

本项目复用了 [Pengyf04/claude-agent-team](https://github.com/Pengyf04/claude-agent-team) 的工作流思想，并针对 Codex Plugin、Skill 和原生 subagent 机制重新实现。

## 许可证

[MIT](LICENSE)
