# GitLab → 飞书 Webhook 中转服务

这是一个使用 FastAPI 编写的 GitLab Webhook 中转服务。多个项目或群组可共用 `POST /webhook/gitlab/{bot_id}`，按路径中的飞书机器人 ID 选择目标机器人，再转换成飞书 `post` 富文本通知。格式和测试以 GitLab 官方事件文档的完整 payload 为依据。

## 事件支持

| 类型 | 消息中的主要信息（请求提供时） |
| --- | --- |
| Push | 项目、分支、操作人、提交作者、可点击的提交哈希、标题和正文、分支创建/删除、提交数量 |
| Tag Push | 标签名、创建/删除、操作人、标签说明、提交记录 |
| 工作项 / 私密工作项 | 编号、标题、类型、动作、状态、描述、创建人、负责人、标签、日期、严重程度、变更摘要 |
| 评论 / 私密评论 | 正文、操作人、动作、关联 MR/工作项/提交/代码片段、评论链接 |
| Merge Request | 编号、标题、动作、描述、创建人、操作人、源/目标分支、标签、负责人、审核人及状态、最后提交、变更摘要 |
| Job | 作业名称、阶段、状态、链接、操作人、提交、流水线、耗时、排队时间、失败原因、重试次数、Runner、环境 |
| Pipeline | 状态、名称、来源、分支、提交、触发人、耗时、阶段、各作业及链接、失败原因、关联 MR、上游流水线 |
| Wiki | 页面标题、内容、动作、操作人、路径、格式、提交说明、版本、差异链接 |
| Deployment | 状态、环境、环境链接、提交、部署人、作业链接；审批事件包含审批人、意见和审批规则 |
| Feature Flag | 名称、描述、启用/关闭状态、操作人 |
| Release | 名称、标签、动作、描述、发布时间、提交、资源及源码下载链接 |
| Milestone | 编号、标题、描述、动作、状态、开始和截止日期、链接 |
| Emoji | 表情名称、授予/撤销、操作人、目标类型、目标链接及关联对象/评论 |
| Resource Access Token | 令牌名称、ID、到期时间、最后使用时间、所属项目/群组、拥有者 ID |
| Resource Deploy Token | 令牌名称、ID、类型、到期时间、撤销状态、所属项目/群组 |
| 群组成员 | 成员、加入/移除/权限修改/访问申请动作、访问级别、到期时间 |
| 项目 / 子群组 | 创建/删除、名称、路径、ID、可见范围、拥有者或父群组 |
| Vulnerability | 标题、状态、严重程度、扫描器、依赖位置、CVE 和关联工作项链接 |
| Duo Flow | 动作、会话链接和状态、触发人、客户端引用、结果或失败原因 |
| System Hook | 系统事件的完整支持清单见下方“系统钩子”章节 |

事件和字段是否触发取决于 GitLab 的版本、套餐及配置。官方页面中的 Duo Flow 是 GitLab 19.4 实验功能，GitLab 19.3 不会因此增加该触发器。群组成员、项目和子群组事件需要配置群组 Webhook。

没有事件头时使用 `object_kind` / `event_name` 识别；未知类型生成通用通知。未提供的字段不会虚构，也不会自动调用 GitLab API：只有创建人 ID 时显示 `user #ID`，只有项目 ID 时显示 `project #ID`。成员事件中的用户是被变更成员，不会误标为操作人。

GitLab Push payload 最多携带最近 20 条提交，服务展示其提供的记录，并提示提交数量差异。过长消息按字符数和 UTF-8 JSON 大小截断，并保留原始链接。飞书 post 中的 Markdown 描述以普通文本显示。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --env-file .env --reload --port 8000
```

Webhook 地址为 `POST /webhook/gitlab/{bot_id}`，健康检查地址为 `GET /health`。`bot_id` 是飞书机器人 Webhook 地址最后的 UUID 部分。

## Docker 部署

```powershell
Copy-Item .env.example .env
# 编辑 .env 后执行
docker compose up -d
```

Compose 支持在 `.env` 中填写 `GITLAB_WEBHOOK_IMAGE`。服务同时配置了镜像名和本地构建上下文，并设置 `pull_policy: missing`：本地已有镜像时直接使用；本地没有时先尝试从镜像仓库拉取，仓库中也没有该镜像时由 Compose 使用当前目录的 Dockerfile 构建同名镜像。默认值为 `gitlab-webhook:latest`。更新本地代码后，使用 `docker compose up -d --build` 强制重建。

在 GitLab 项目 Webhook、群组 Webhook 或 System Hook 中设置对应机器人的 URL，例如：

```text
http://104.238.221.47:7001/webhook/gitlab/c33921dd-bd41-415a-b4cb-7b1339da8e86
```

将 `/hook/` 后的机器人 ID 替换为目标机器人的 ID。不同项目可以使用不同 ID 发往不同机器人；相同 ID 则共用一个机器人。Secret Token 设置为 `GITLAB_SECRET_TOKEN`。服务通过 `X-Gitlab-Event` 区分事件，通过 `X-Gitlab-Token` 校验来源。System Hook 也使用带机器人 ID 的统一路径。

URL 中的机器人 ID 属于凭据信息，应限制 GitLab Webhook 配置和服务访问日志的查看权限。服务只接受 UUID 格式的 ID，并固定请求 `open.feishu.cn`，不会把请求路径当作任意目标 URL。若服务部署在使用 Lark 而非 Feishu 的租户环境，需要调整固定的机器人 Webhook 域名。

项目 Webhook 提供完整的 Push/MR/CI 等事件。System Hook 的 `repository_update` 主要提供仓库引用变化，无法直接还原提交正文。两者同时订阅可能产生两条不同类型的通知。

代码升级后，在部署目录执行 `docker compose up -d --build` 重建并更新容器，`docker compose restart` 不会把宿主机的新代码打包进已有镜像。端口以现有 compose 配置为准；对外使用 7001 时映射为 `7001:8000`。

## 配置

完整配置见 `.env.example`。无需设置 `FEISHU_WEBHOOK_URL`，目标 URL 会根据请求路径中的机器人 ID 拼接。`FEISHU_RETRY_COUNT=3` 表示首次发送失败后再重试 3 次。`FEISHU_SECRET` 可留空；配置后会附加机器人签名。

## 系统钩子

依据保存的 [GitLab System Hooks 官方文档](https://docs.gitlab.com/administration/system_hooks/)，为 `X-Gitlab-Event: System Hook` 单独适配全部 30 个文档事件类型。所有收到的系统事件均可发送飞书；已撤销此前的过滤逻辑，旧 `.env` 中的 `SYSTEM_HOOK_EVENTS` 不再生效。

| 类型 | 事件名称 | 展示内容 |
| --- | --- | --- |
| 用户 | `user_create`、`user_destroy`、`user_rename`、`user_failed_login` | 创建/删除/改名/登录失败、姓名、用户名、ID、旧用户名和账户状态 |
| SSH 密钥 | `key_create`、`key_destroy` | 添加/删除、所属用户名、密钥 ID、算法、SHA256 指纹和注释，不输出完整公钥 |
| 项目 | `project_create`、`project_destroy`、`project_rename`、`project_transfer`、`project_update` | 项目名称、路径、ID、命名空间 ID、可见范围、拥有者、迁移或改名前后的路径 |
| 群组 | `group_create`、`group_destroy`、`group_rename` | 名称、路径、完整路径、ID、旧路径 |
| 项目成员 | `user_add_to_team`、`user_remove_from_team`、`user_update_for_team` | 项目、被变更成员姓名/用户名/ID、权限级别 |
| 群组成员 | `user_add_to_group`、`user_remove_from_group`、`user_update_for_group` | 群组、被变更成员姓名/用户名/ID、权限级别 |
| 访问申请 | `user_access_request_to_project`、`user_access_request_revoked_for_project`、`user_access_request_to_group`、`user_access_request_revoked_for_group` | 申请或撤销、目标项目/群组、申请人、权限级别 |
| 角色升级审批 | `gitlab_subscription_member_approval`、`gitlab_subscription_member_approvals` | 申请/通过/拒绝、用户 ID、申请人/审核人 ID、原/新角色、命名空间、审批状态、应用失败的请求 ID |
| Push / Tag | `push`、`tag_push` | 项目、分支或标签、创建/删除、操作者、前后哈希；请求有提交记录时展示作者、说明和链接 |
| MR | `merge_request` | 使用现有 MR 模板，展示标题、动作、源/目标分支、操作者、创建人 ID、负责人、标签、最后提交和状态 |
| 仓库更新 | `repository_update` | 项目、操作人、逐项分支/标签变更、前后哈希和已知提交链接 |

用户和密钥等实例事件不再显示 `unknown-project`。用户/成员/密钥所有者是事件对象，不能据此推断操作人；审批用 `requested_by_user_id` 或 `reviewed_by_user_id` 表示实际申请/审核人。请求提供时间时显示原始时间。未提供的姓名、提交说明等不会虚构，也不额外调用 GitLab API。

例如用户创建通知：

```text
GitLab System · User created
User created: 张一龙 (@zyl)
User Id: 13
Created At: 2026-10-05T08:07:27Z
Updated At: 2026-10-05T08:07:27Z
```

系统钩子会自动发送用户创建/删除等基础事件。勾选“仓库更新”是额外启用仓库事件，并不会关闭基础事件。系统 Push/Tag 不保证携带提交详情；没有 `commits` 时仍显示分支、哈希及已知提交链接。未知系统事件使用独立通用通知，不再套用项目模板。

## 测试

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

`tests/fixtures/official_events.json` 保存从本地官方网页提取的 31 份完整示例，并记录来源 URL。测试逐类验证关键文字、链接、请求头及无请求头解析，并验证真实接口进入发送流程；发送使用 mock，不向真实飞书群发消息。私密事件和动作变化使用基于官方样例的派生测试。

`tests/fixtures/official_system_events.json` 保存系统钩子文档的 31 份完整示例及事件清单，覆盖全部 30 个事件类型（角色审批有通过/拒绝两份样例）。测试核对具体字段、用户与操作人区别、指纹计算及每类事件的接口发送流程。

更新官方样例时，将官方页面保存为 HTML 后运行：

```powershell
python scripts/extract_official_samples.py "Webhook events _ GitLab Docs.html" tests/fixtures/official_events.json
python scripts/extract_official_samples.py "System hooks _ GitLab Docs.html" tests/fixtures/official_system_events.json --system
python -m pytest -q
```

当前去重记录是单进程内存缓存，重启会清空；这次更新没有增加消息队列或跨 worker 去重。尚未对真实 GitLab/飞书做完整联调或并发压测。

