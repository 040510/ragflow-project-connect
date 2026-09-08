# RAGFlow Project Connect

让 Agent 通过只读 MCP 连接企业内网中选定项目的 RAGFlow 知识库。

## 一句话安装

把下面这句话发给支持本地命令执行的 Agent：

```text
帮我安装这个 Skill：npx skills add 040510/ragflow-project-connect --skill ragflow-project-connect -y -g
```

安装需要 Node.js/npm、Git，以及 GitHub 和 npm 网络访问。Skill 的连接脚本需要 Python 3.10+，不需要额外 pip 依赖。

`skills` 安装器的安装位置取决于其对当前 Agent 的支持和识别结果。确认目标 Agent 已识别 `ragflow-project-connect`；若未识别，按该 Agent 的 Skill 安装方式导入完整文件夹。不要只导入 SKILL.md。

## 创建连接

安装 Skill 不会自动绑定任何知识库。安装完成后对 Agent 说：

```text
使用 ragflow-project-connect，为我的 WorkBuddy 创建 RAGFlow MCP 连接。
先列出项目，再列出我选定项目下的知识库，让我选择范围后再创建连接。
```

Skill 会加载附带的 CA 公共证书，创建独立连接凭据，检查 MCP 初始化和工具定义，然后备份并更新客户端配置。用户无需手动安装 CA、领取控制凭据或提供 RAGFlow 密码。提供测试问题后可进一步验证真实检索。

当前自动配置支持 WorkBuddy 和采用兼容 JSON 配置格式的客户端。其他客户端需要核对配置格式，且必须支持本地 stdio MCP。

配置完成后重新加载客户端 MCP，必要时重启 Agent。之后直接提问：

```text
请检索已连接的知识库，回答我的问题，并注明来源。
```

## 连接方式与范围

```text
Agent -> 本地 stdio 适配器 -> HTTPS 内网网关 -> 选定的 RAGFlow 知识库
```

- 默认网关：`https://172.16.3.173:8892`。这是企业内网服务，不是公共试用接口。
- 检索时必须能从网关允许的内网访问；从 GitHub 安装成功并不代表已有内网连接。
- 网关仅展示已配置项目及其可用知识库，不展示未归属项目的知识库。
- 已绑定知识库新增文档，在 RAGFlow 完成解析、向量化和索引后，原连接即可检索。
- 项目中新加入的知识库不会自动扩大现有连接范围，需要重新选择并建立连接。
- 凭据有有效期；可在过期前轮换，过期或撤销后应重新创建连接。
- 目录范围受网关服务账号在 RAGFlow 中的可见性约束，不自动跨越租户隔离。
- 当前自助接入以指定内网来源为边界，不使用企微 SSO 对每个用户筛选项目权限。

Skill 用于创建、检查、轮换和断开连接；日常检索使用安装好的 MCP 工具 `ragflow_project_retrieval`。

## 安全说明

本仓库有意公开 Skill 代码、内网连接配置和 CA 公共证书，不包含 CA 私钥、RAGFlow API Token、管理员凭据、用户连接凭据或知识库文档。

CA 公共证书仅由适配器用于验证这个网关，不需要导入操作系统信任库。不要关闭 TLS 校验，不要上传用户的 `~/.ragflow-project-connect/connections/` 目录。不要把自助接入网关开放到公网。

详细流程见 [SKILL.md](SKILL.md)。
