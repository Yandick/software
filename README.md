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

- 前端：http://127.0.0.1:5666
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
| `VLLM_GPU_MEMORY_UTILIZATION` | `0.90` | vLLM 显存利用率 |
| `VLLM_MAX_MODEL_LEN` | `40960` | vLLM 最大上下文长度 |
| `OPS_MODEL_PATH` | `models/qwen3-1.7b` | 本地模型路径 |
| `OPS_VLLM_MODEL_NAME` | `qwen3-1.7b` | vLLM served model name |
| `BACKEND_PORT` | `8010` | 后端端口 |
| `FRONTEND_PORT` | `5666` | 前端端口 |

## 演示账号

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 管理员 | `admin` | `admin123` |
| 运维人员 | `ops` | `ops123` |
| 普通用户 | `user` | `user123` |
| 审计员 | `auditor` | `audit123` |

## 依赖检查

```bash
python scripts/check_dependencies.py
```

如果缺少依赖，按提示安装即可。项目不绑定固定 conda 环境名；只要求在同一个已激活环境中具备 Python、vLLM、Node.js 和 pnpm。

## 开发与任务文档

- `docs/task-checklist.md`：任务清单和完成度，每次继续开发前先看这里。
- `docs/code-style.md`：代码与注释规范。
- `docs/dependencies.md`：依赖说明。
- `docs/development-review.md`：审查与整改记录。
- `agent.md`：开发约定和已沉淀知识。

## qwen-agent 说明

项目已记录 qwen-agent 作为后续 Agent Workflow 能力的依赖。当前主问答链路是 RAG + vLLM；后续接入 qwen-agent 时，应按 ReAct loop 设计，并且工具必须走受控后端 API、RBAC 和审计日志，不能让 Agent 直接执行账号冻结、解冻或权限变更等高风险操作。

qwen-agent 官方仓库：https://github.com/QwenLM/Qwen-Agent
