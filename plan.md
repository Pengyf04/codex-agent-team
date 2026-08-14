# Codex Agent Team 公开发布实施计划

批准日期：2026-08-13

## 目标

把现有 Agent Team 工作流整理为可从公开 GitHub 仓库安装的 Codex Plugin。Plugin 内置 `$agent-team` Skill、四个真实 subagent 角色协议和零第三方运行依赖的 Python 状态机，使其他用户可以安装、显式调用、恢复、验证和审计完整工作流。

公开仓库目标为 `Pengyf04/codex-agent-team`。本计划只授权本地整理、加固和验证；创建远程仓库、首次提交、推送及 GitHub Release 必须在本地验收完成后取得单独的“批准公开发布”。

## 已确认的发布决策

1. 公开作者使用 `Pengyf04`，不公开真实姓名或个人邮箱。
2. 交付形态为 Codex Plugin 外壳加内置 `agent-team` Skill，而不是裸 `SKILL.md`。
3. macOS 和 Linux 为首版正式支持平台；Windows 首版标记为实验性，完整安装验证通过后再升级为正式支持。
4. `.agent-team/` 运行记录默认保留，用于恢复、审计和追溯；不自动删除，清理由用户显式触发。
5. 使用 MIT License，版权身份为 `Pengyf04`。
6. 安全漏洞通过 GitHub Private Vulnerability Reporting 接收。
7. 采用两阶段授权：本地实现与验证通过后，再申请公开发布批准。

## 第一性原理成功标准

- 全新 Codex 环境能够从仓库 Marketplace 安装 Plugin，并在新任务中显式调用 `$agent-team`。
- Planner、Plan Reviewer、Executor 和 Verifier 是职责隔离的真实 subagent；未通过人工卡点 A 不得执行产品写入。
- Verifier 发现缺陷、关键临时测试或未关闭风险时，工作流必须回到 Executor 并形成可审计闭环。
- 关键附加测试必须进入标准验证命令可达的正式测试，才能支撑 `PASS` 或风险关闭。
- 风险生命周期、Executor 三层执行轮次和 `DONE` 完成范围均持久化、可恢复、可验证。
- 运行时只要求 Python 3.10+ 和支持 Plugins/subagent 的 Codex，不要求 MCP Server、第三方 Python 包或额外 API Key。
- 待发布文件、Git 历史、文档、示例和 CI 不包含凭据、私人邮箱、真实姓名、Cookie、Token 或本机绝对路径。

## 目标仓库结构

```text
codex-agent-team/
├── .agents/plugins/marketplace.json
├── .github/
│   ├── dependabot.yml
│   └── workflows/ci.yml
├── docs/
│   ├── architecture.md
│   ├── development-and-release.md
│   └── security-and-privacy.md
├── plugins/codex-agent-team/
│   ├── .codex-plugin/plugin.json
│   └── skills/agent-team/
│       ├── SKILL.md
│       ├── agents/openai.yaml
│       ├── references/
│       └── scripts/state.py
├── scripts/validate.py
├── tests/
│   ├── test_adversarial.py
│   ├── test_package_install.py
│   ├── test_permissions.py
│   └── test_state.py
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
├── README.md
├── README.zh-CN.md
├── SECURITY.md
├── requirements-dev.txt
└── plan.md
```

## 实施步骤

### 1. 发布基线整理

- 将 Plugin 版本规范化为严格 SemVer `0.1.0`；本地 cachebuster 只用于开发重装，不进入正式 Release。
- 保持 Plugin 名、目录名、Marketplace 名和 Skill 名一致。
- 扩充 `.gitignore`，排除 Agent Team 运行记录、Python/质量工具缓存和常见本机文件。
- 清理公开方案中的内部开发流水账，仅保留本计划和可验证设计决策。
- 增加固定版本的开发依赖清单，运行时仍保持 Python 标准库零第三方依赖。

### 2. 本地数据与权限加固

- POSIX 环境显式收紧 `.agent-team/`、`runs/` 和每个 run 目录为 `0700`。
- 状态、锁和临时请求文件尽可能使用 `0600`；不只依赖用户 `umask`。
- Windows 保留兼容行为，并明确 POSIX 权限位不等价于 Windows ACL。
- 保持符号链接逃逸、路径穿越、伪造 run 目录和并发请求防护。
- 当 `.agent-team/` 已被 Git 跟踪时停止工作流；仅未被忽略时提示用户，不擅自修改目标项目。
- 记录并公开说明运行数据内容、保留方式和显式清理方法。

### 3. 文档和合规材料

- 中英文 README 覆盖安装、调用、双人工卡点、交付模式、中断恢复、更新、卸载、成本、支持范围和故障排查。
- `SECURITY.md` 说明漏洞报告、威胁模型、安全边界和凭据泄露处置。
- `docs/security-and-privacy.md` 说明本地数据、权限、网络/遥测、保留和清理。
- `docs/architecture.md` 说明四角色、状态机、证据门禁和非目标。
- `CONTRIBUTING.md` 与 `docs/development-and-release.md` 说明开发、验证和维护者发布流程。
- `CHANGELOG.md` 从 `0.1.0` 开始记录公开版本。

### 4. 测试和 CI

- 保留现有状态机与对抗性测试，新增权限、打包清单、版本一致性和敏感文件排除测试。
- CI 覆盖 Ubuntu Python 3.10/3.12/3.14、macOS Python 3.12 和 Windows Python 3.12。
- 运行仓库 validator、固定 OpenAI commit 的 Skill/Plugin validator、Ruff、Mypy strict、Bandit 和秘密扫描。
- GitHub Actions 使用最小权限并固定第三方 Action SHA。
- Windows 只有在真实 Marketplace 安装与调用验证成功后才升级为正式支持。

### 5. 全新环境部署闭环

- 使用临时 `HOME`/`CODEX_HOME` 从本地 repo Marketplace 完成安装、列表发现、卸载、重装和更新验证。
- 在新 Codex 任务中确认 Skill 加载；安装或更新后不得使用旧任务作为加载证据。
- 运行最小真实工作流，验证卡点 A、Verifier 回修、关键测试固化、Planner 终验、卡点 B 和 `DONE` 完成范围。
- 对比源 Plugin 与安装缓存，排除旧缓存误判。
- 仓库公开后再从真实 GitHub 地址重复安装验收。

### 6. 发布前对抗性审查

- 审查待提交文件清单、Git 状态和完整秘密/PII/主机路径扫描结果。
- 确认没有 `.agent-team/`、缓存、构建产物、日志、截图或未声明二进制文件。
- 确认 Actions 不向不可信 PR 暴露 Secret，文档命令不执行宽范围破坏操作。
- 复核许可证、来源说明、支持范围、已知限制和全部残余风险。
- 只有所有本地门禁通过，才提交发布前报告并请求第二阶段授权。

## 发布步骤（需第二阶段明确授权）

1. 建立首次干净 commit。
2. 创建公开仓库 `Pengyf04/codex-agent-team` 并推送 `main`。
3. 启用 Issues、Private Vulnerability Reporting、Secret Scanning、Push Protection 和分支保护。
4. 创建 `v0.1.0` tag 与 GitHub Release。
5. 从公开 GitHub 地址重新安装并复跑发布验收。

## Definition of Done

- 官方 Skill/Plugin validator、仓库 validator、全部单元/对抗性测试和静态安全门禁通过。
- POSIX 权限测试以及 macOS/Linux 全新环境安装、调用、更新和卸载测试通过。
- Windows CI 通过，且文档仍准确标记实验性，除非真实安装验收另有证据。
- Plugin manifest、Marketplace、文档和 Release 版本一致。
- 待发布内容及 Git 历史无秘密、个人邮箱、真实姓名或本机绝对路径。
- 一条真实 Agent Team 工作流证明双人工卡点和开发—验证—回修闭环。
- 本地验收报告列出完成范围、排除范围、部署状态和残余风险。
- 在第二阶段批准后，公开仓库和 `v0.1.0` Release 可访问且可从 GitHub 安装。

## 回滚原则

- 首次推送前发现问题：不创建公开远程，不让敏感内容进入永久历史。
- 发布后发现普通缺陷：发布新的 SemVer 修复版本，不覆盖既有 tag。
- 凭据误入历史：先吊销和轮换凭据，再清理 Git 历史；删除文件不能代替轮换。
- 严重安全问题通过私密漏洞渠道协调修复，修复前不公开利用细节。

## 本地实施状态

截至 2026-08-13，本地公开发布候选已通过独立只读验收。详细证据、已固化缺陷和未完成的宿主/公开环境门禁见
[`docs/pre-release-report.md`](docs/pre-release-report.md)。用户随后已批准创建 Public 仓库、首次 commit/push、分支保护和安全设置；`v0.1.0` tag 与 GitHub Release 仍未获授权。

## 2026-08-14 真实工作流监控后的优化计划

### 目标

- 在人工卡点 B 之前严格预检并暂存规范化的 `completion_scope`，避免字段拼写或重建错误导致重复审批。
- 让最终批准直接消费用户已经看到的暂存范围，并将它绑定到当前方案、验证和最终审查证据。
- 在状态摘要、人工卡点和最终交付中显式提供 canonical 绝对输出路径及含义清晰的证据哈希。
- 将初始化前的验收标准确认明确标注为前置确认，不与人工卡点 A/B 混淆。

### 实施范围

1. `state.py` 增加最终范围预检/暂存请求、相应状态约束和摘要字段；状态 schema 升至 6。
2. 最终批准不再重新接收手工构造的范围，而是消费状态机中已预检并与最终证据绑定的范围。
3. 更新 Agent Team Skill、协议、请求示例、中英文 README、变更日志和预发布说明。
4. 增加正常路径、错误字段、证据漂移、重新预检、绝对路径和哈希语义的正式回归与对抗性测试。
5. 运行仓库验证、全部 unittest、Ruff、Mypy strict、Bandit、固定 OpenAI validator、本地安装/更新/卸载/重装 smoke 和完整临时状态链。

### 完成边界

- 本轮只修改本地公开候选仓库并形成自测报告。
- 未获用户最终确认前，不 commit、push、创建 tag 或 GitHub Release。

### 最终授权

- 2026-08-14，用户已最终确认提交并推送本轮优化到现有 Public 仓库的 `main`。
- 本次授权不包含创建 tag 或 GitHub Release。
