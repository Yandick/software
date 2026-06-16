# 运维数字员工系统

一个面向企业运维场景的 AI 数字员工课程项目：本地 Qwen3 大模型、RAG 私有知识库、运维申告门户、在线记录/转人工、人工处理与回访、知识库沉淀、运维账号管理、JWT/RBAC 和审计统计。

## 功能概览

- 本地大模型：使用 vLLM 启动 Qwen3，并通过 OpenAI-compatible API 提供推理服务。
- RAG 知识库：内置运维 FAQ/Runbook 种子知识，问答返回引用来源。
- 运维申告门户：用户可自助提问，无法解决时创建在线记录或转人工。
- 人工处理闭环：运维人员处理问题、回访用户，确认解决后沉淀为知识案例。
- 账号管理后台：支持运维账号新增、冻结、解冻、修改、查询，并记录审计日志。
- 权限与审计：JWT 登录、RBAC 角色权限、问答日志、账号操作日志、知识变更日志。

## 技术栈

- 后端：FastAPI、SQLite、PyJWT、httpx
- 前端：Vue / Vben Admin / pnpm
- 模型服务：vLLM + Qwen3
- 模型生态依赖：torch、transformers、qwen-agent

> `transformers` 是 Qwen/vLLM 环境的重要依赖和常用 NLP 库；本项目的在线问答服务入口仍然是 vLLM，不绕过 vLLM 直接推理。

## 目录结构

```text
software/
├── backend/                 # FastAPI 后端
│   └── app/
│       ├── data/knowledge_seed.json
│       ├── main.py
│       ├── database.py
│       ├── security.py
│       └── services/
├── frontend/                # Vue Vben Admin 前端
├── models/qwen3-1.7b         # 本地模型目录
├── docs/                     # 开发、依赖、任务清单文档
├── scripts/start_all.sh      # 一键启动脚本
├── scripts/stop_all.sh       # 停止脚本
├── scripts/package_check.py   # 离线交付包装检查
└── requirements-minimal.txt  # Python 最小依赖
```

## 环境准备

### 1. 创建并激活 conda 环境

环境名可以自定义，示例使用 `ops-employee`：

```bash
conda create -n ops-employee python=3.10 -y
conda activate ops-employee
```

### 2. 安装 Python 依赖

```bash
pip install -r requirements-minimal.txt
```

### 3. 安装 Node.js 和 pnpm

前端需要 Node.js 和 pnpm。推荐 Node 20/22/24。

如果使用 conda 安装 Node.js：

```bash
conda install -y nodejs
npm install -g pnpm@10.33.0
```

检查命令：

```bash
python --version
node --version
npm --version
pnpm --version
```

### 4. 安装前端依赖

首次运行可交给一键脚本安装，也可以手动安装：

```bash
cd frontend
pnpm install
cd ..
```

## 启动项目

在项目根目录执行：

```bash
./scripts/start_all.sh
```

默认服务地址：

- 用户服务门户：http://127.0.0.1:5666/portal
- 工作人员管理台：http://127.0.0.1:5666/staff/login
- 后端：http://127.0.0.1:8010/api/health
- vLLM：http://127.0.0.1:8000/v1/models

首次启动并安装前端依赖：

```bash
./scripts/start_all.sh --install-frontend
```

只启动 vLLM + 后端：

```bash
./scripts/start_all.sh --no-frontend
```

停止服务：

```bash
./scripts/stop_all.sh
```

启动脚本统一写入 `.run/frontend.pid`、`.run/backend.pid`、`.run/vllm.pid`；停止脚本也会兼容清理历史的 `*-local.pid`/`*-portal.pid`，并按 `FRONTEND_PORT`、`BACKEND_PORT`、`OPS_VLLM_PORT` 检查和释放本项目占用的端口。

本地生产效果按双入口组织：

- 访问 `http://127.0.0.1:5666/` 会进入面向业务用户的服务门户，而不是后台登录页。
- `/portal` 提供“用户 / 工作人员”两种身份登录；普通用户登录后只看到数字员工咨询、在线记录提交和本人处理进度。
- 运维、管理员和审计员可在 `/portal` 选择“工作人员”登录，也可以直接从 `/staff/login` 登录，进入 `/ops/*` 管理界面。
- 工作人员如果从用户门户误登录，会自动进入管理台；普通用户如果从工作人员入口误登录，会回到服务门户。
- 登录后的用户门户和工作人员管理台右上角都提供一致位置的“门户首页 / 切换身份”操作。

## GPU 与显存参数

默认使用 GPU 0。可指定 GPU：

```bash
CUDA_VISIBLE_DEVICES=6 ./scripts/start_all.sh
```

显存紧张时可降低 vLLM 显存比例或上下文长度：

```bash
VLLM_GPU_MEMORY_UTILIZATION=0.55 CUDA_VISIBLE_DEVICES=6 ./scripts/start_all.sh
```

```bash
VLLM_MAX_MODEL_LEN=8192 VLLM_GPU_MEMORY_UTILIZATION=0.35 CUDA_VISIBLE_DEVICES=6 ./scripts/start_all.sh
```

脚本启动前会检查第一张可见 GPU 的空闲显存，不足会提前退出并提示换卡或降低参数。

## 配置项

常用环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CUDA_VISIBLE_DEVICES` | `0` | 指定可见 GPU |
| `VLLM_GPU_MEMORY_UTILIZATION` | `0.55` | vLLM 显存利用率 |
| `OPS_CORS_ORIGINS` | `http://127.0.0.1:5666,http://localhost:5666` | 允许访问后端 API 的前端来源，生产环境不要设为 `*` |
| `OPS_APP_VERSION` | `1.0.0-demo` | 后端健康检查和系统信息展示的交付版本号 |
| `VLLM_MAX_MODEL_LEN` | `40960` | vLLM 最大上下文长度 |
| `OPS_MODEL_PATH` | `models/qwen3-1.7b` | 本地模型路径 |
| `OPS_VLLM_MODEL_NAME` | `qwen3-1.7b` | vLLM served model name |
| `OPS_ENVIRONMENT` | `development` | 运行环境；设为 `production` 时启用安全配置校验 |
| `OPS_JWT_SECRET` | 开发默认值 | JWT 签名密钥；生产环境必须设置为非默认且不少于 32 字符 |
| `OPS_SEED_DEMO_ACCOUNTS` | `true` | 是否写入内置演示账号；生产环境必须设为 `false` |
| `OPS_ADMIN_PASSWORD` | 无 | `scripts/create_admin.py` 非交互模式读取的管理员初始密码 |
| `BACKEND_PORT` | `8010` | 后端端口 |
| `FRONTEND_PORT` | `5666` | 前端端口 |

配置样例见 `.env.example`。生产部署说明见 `docs/deployment.md`。

## 演示账号

以下账号只用于课程 Demo。生产部署请设置 `OPS_ENVIRONMENT=production`、配置强随机 `OPS_JWT_SECRET`，并设置 `OPS_SEED_DEMO_ACCOUNTS=false`。

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 管理员 | `admin` | `admin123` |
| 运维人员 | `ops` | `ops123` |
| 普通用户 | `user` | `user123` |
| 审计员 | `auditor` | `audit123` |

入口建议：

- 普通用户：打开 `/portal`，使用 `user / user123`。
- 工作人员：打开 `/staff/login`，使用 `admin / admin123`、`ops / ops123` 或 `auditor / audit123`。

关闭内置 Demo 账号 seed 后，可用离线脚本创建第一个生产管理员：

```bash
OPS_ENVIRONMENT=production \
OPS_SEED_DEMO_ACCOUNTS=false \
OPS_JWT_SECRET=<生产 JWT secret> \
python scripts/create_admin.py --username admin --real-name "系统管理员" --department "信息技术部"
```

默认不会覆盖已有账号；需要轮换同名管理员密码时显式加 `--replace`。

## 依赖检查

```bash
python scripts/check_dependencies.py
```

如果缺少依赖，按提示安装即可。项目不绑定固定 conda 环境名；只要求在同一个已激活环境中具备 Python、vLLM、Node.js 和 pnpm。

## 系统健康与包装检查

离线交付包检查不需要启动后端或 GPU：

```bash
python scripts/package_check.py
```

如果要同时跑后端 pytest：

```bash
python scripts/package_check.py --run-tests
```

运行中的服务提供三个系统检查入口：

- `/api/health`：公开进程存活检查，返回应用名和版本号。
- `/api/ready`：公开后端就绪检查，覆盖配置和 SQLite schema。
- `/api/ready?include_llm=true&require_llm=true`：完整链路就绪检查，额外要求 vLLM 可用。
- `/api/system/info`：登录后查看系统版本、前端包、数据库类型、模型路径和功能开关，不返回密钥。

## 工程测试与迁移

后端正式测试入口为 pytest：

```bash
python -m pytest
```

当前测试使用临时 SQLite 和 mock LLM，不依赖真实 vLLM/GPU。旧脚本入口仍保留：

```bash
python scripts/test_agent_react.py
```

数据库迁移已建立 Alembic baseline，新部署可运行：

```bash
alembic upgrade head
```

旧演示库如果已经由 `init_db()` 创建过完整表结构，可评估后执行：

```bash
alembic stamp head
```

密码存储已从单次 SHA-256 升级为 Argon2id；旧 SHA-256 哈希仍可登录，登录成功后会自动升级。

## 交付前验收

服务启动后可运行一键验收脚本：

```bash
python scripts/run_acceptance.py
```

该脚本会检查后端健康、后端就绪、vLLM 就绪、RAG smoke test、知识敏感信息检查、脚本化闭环 Demo 和审计 CSV 导出。若刚更新过代码，请先重启后端；详细清单见 `docs/final-acceptance-checklist.md`。

## 手动多角色录屏流程

答辩录屏优先推荐按 `docs/demo-script.md` 手动操作。流程会真实打开用户门户和后台处理台，由你依次扮演普通用户、运维人员、管理员和审计员，完成用户提问、数字员工 RAG 回答、转人工、运维处理、用户查看处理结果、知识发布和审计展示。

推荐问题和处理话术已写在文档里，正式录制时直接使用本地电脑的屏幕录制工具即可。

## 单页多角色闭环 Demo

管理员从 `/staff/login` 登录后进入 `/ops/demo`，即可在一个浏览器页面内查看普通用户、数字员工、运维人员和管理员/审计员四个角色视角。该页面保留为监控大屏和备用演示入口，支持重置链路、单步执行和自动推进，固定演示 VPN 证书过期、账号解冻审批和知识边界三类预置问题。

```bash
./scripts/start_all.sh
```

服务启动后访问前端，使用 `admin / admin123` 在 `/staff/login` 登录并打开 `/ops/demo`。

## 开发与任务文档

- `docs/current-todo.md`：当前项目整理和按优先级排列的待办清单。
- `docs/task-checklist.md`：任务清单和完成度，每次继续开发前先看这里。
- `docs/final-acceptance-checklist.md`：正式录屏和答辩前的一键验收清单。
- `docs/deployment.md`：生产配置、数据库迁移、管理员初始化、健康检查和 systemd 示例。
- `docs/engineering-standards.md`：工程分层、测试、迁移和安全规范。
- `docs/code-style.md`：代码与注释规范。
- `docs/dependencies.md`：依赖说明。
- `docs/development-review.md`：审查与整改记录。
- `agent.md`：开发约定和已沉淀知识。

## qwen-agent 说明

项目已记录 qwen-agent 作为后续 Agent Workflow 能力的依赖。当前主问答链路是 RAG + vLLM；后续接入 qwen-agent 时，应按 ReAct loop 设计，并且工具必须走受控后端 API、RBAC 和审计日志，不能让 Agent 直接执行账号冻结、解冻或权限变更等高风险操作。

qwen-agent 官方仓库：https://github.com/QwenLM/Qwen-Agent
