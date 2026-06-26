# 运维数字员工系统

一个面向企业运维场景的 AI 数字员工课程项目：本地 Qwen3 大模型、RAG 私有知识库、运维申告门户、在线记录/转人工、人工处理与回访、知识库沉淀、运维账号管理、JWT/RBAC 和审计统计。

## 功能概览

- 本地大模型：使用 vLLM 启动 Qwen3，并通过 OpenAI-compatible API 提供推理服务。
- RAG 知识库：内置运维 FAQ/Runbook 种子知识，使用本地 Qwen3-Embedding 做 hybrid RAG，问答返回引用来源。
- 运维申告门户：用户可自助提问，无法解决时创建在线记录或转人工。
- 人工处理闭环：运维人员处理问题、回访用户，确认解决后沉淀为知识案例。
- 账号管理后台：支持运维账号新增、冻结、解冻、修改、查询，并记录审计日志。
- 权限与审计：JWT 登录、RBAC 角色权限、问答日志、账号操作日志、知识变更日志。

## 技术栈

- 后端：FastAPI、SQLite、PyJWT、httpx、numpy、FAISS
- 前端：Vue / Vben Admin / pnpm
- 模型服务：vLLM + Qwen3
- 模型生态依赖：torch、transformers、qwen-agent

> `transformers` 是 Qwen/vLLM 环境的重要依赖和常用 NLP 库；本项目的在线问答服务入口仍然是 vLLM，不绕过 vLLM 直接推理。
> RAG embedding index 默认落在本地磁盘，不需要额外部署 Qdrant。当前推荐安装 `faiss-cpu`：系统会持久化 numpy 向量矩阵，并用本地 FAISS `IndexFlatIP` 做 dense candidate recall；FAISS 不可用时才降级到 numpy 矩阵检索。

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

开发或本地调试时，在项目根目录执行：

```bash
./scripts/start_all.sh
```

部署或答辩验收时，建议直接用部署脚本，只需要指定 base model 和 embedding model 的 GPU：

```bash
./scripts/start_deploy.sh --base-cuda-devices 0,1 --embedding-cuda-devices 2
```

这个入口默认会开启真实 subagent LLM 审阅，并保留 `--no-frontend`、`--install-frontend` 等 `start_all.sh` 参数透传。

默认服务地址：

- 用户服务门户：http://127.0.0.1:5666/portal
- 统一登录门户：http://127.0.0.1:5666/portal
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
- 运维、管理员和审计员在 `/portal` 选择“工作人员”登录后进入 `/ops/*` 管理界面。
- 历史 `/staff/login` 和 `/auth/login` 链接只做兼容跳转，不再提供独立登录页。
- 登录后的用户门户和工作人员管理台右上角都提供一致位置的“门户首页 / 切换身份”操作。

## 意图路由与无关问题处理

QA 入口现在先经过 `intent_router` subagent，再决定是否进入 RAG 和后续运维 workflow。这个实现迁移的是 Rasa `out_of_scope/fallback` 与 Haystack `ConditionalRouter` 的工程范式，而不是直接引入它们的 runtime：

- `greeting`、`thanks`、`goodbye`、`capability`、`low_information`、`out_of_scope` 会直接返回短答案，不触发 embedding/RAG、ticket draft 或 subagent 审阅。
- `ops_support` 会进入 FAISS/Qwen3-Embedding hybrid RAG，再由 supervisor、risk_guardian、ops_employee、knowledge_curator、evaluator 编排。
- `controlled_operation` 会继续进入受控风险链路，只允许流程指导和转人工，不允许直接执行账号、权限、生产、数据库、删除、提权或绕过审批动作。
- 默认使用确定性规则，保证离线测试和演示稳定；`OPS_ENABLE_INTENT_ROUTER_LLM=true` 后，本地 Qwen 只在规则不确定时输出 JSON 路由建议，确定性安全门控仍是最终权威。

这里的 “training” 不是训练一个 agent 记忆，也不是把 Qwen base model 重新训练一遍。Rasa 类系统通常是用真实会话样本训练一个较小的 intent/entity 分类器，并用 fallback policy 处理低置信度输入；Haystack ConditionalRouter 则是按条件把请求分到不同 pipeline。项目当前采用工程迁移版：

```text
user input
  -> intent_router: scope / low-info / out-of-scope / controlled / ops-support
  -> ops-support only: FAISS + Qwen3-Embedding RAG
  -> Qwen base model: grounded answer or structured subagent review
  -> deterministic risk gate / RBAC / audit
```

因此继续部署同一个 Qwen base model 就可以：路由层负责“该不该让它回答”，RAG/FAISS 负责“可引用知识 memory”，Qwen 负责“基于证据生成回答或 JSON 审阅”。后续如果积累了足够真实会话，可以把 `intent_router` 的规则升级为本地轻量分类器或 LoRA/embedding selector，但不建议一开始就微调 base model。

对应文件：

- `backend/app/services/intent_router_service.py`
- `backend/app/agents/intent_router/prompt.md`

## GPU 与显存参数

默认兼容 `CUDA_VISIBLE_DEVICES`，它现在表示 base model/vLLM 使用的 GPU。推荐显式区分 base model 和 embedding model：

```bash
VLLM_CUDA_VISIBLE_DEVICES=0,1 \
OPS_EMBEDDING_CUDA_VISIBLE_DEVICES=2 \
./scripts/start_all.sh
```

上面表示：

- Qwen base model 由 vLLM 启动在 GPU 0、1，并自动设置 `VLLM_TENSOR_PARALLEL_SIZE=2`。
- Qwen3-Embedding 后端进程只看到 GPU 2；`OPS_EMBEDDING_DEVICE=auto` 即会加载到这张卡上。

也可以用命令行参数指定：

```bash
./scripts/start_all.sh --vllm-cuda-devices 0,1 --embedding-cuda-devices 2
```

如果继续使用旧写法，下面仍然表示 vLLM/base model 用 GPU 0、1：

```bash
CUDA_VISIBLE_DEVICES=0,1 OPS_EMBEDDING_CUDA_VISIBLE_DEVICES=2 ./scripts/start_all.sh
```

`VLLM_GPU_MEMORY_UTILIZATION` 未设置时，启动脚本会从 `OPS_MODEL_PATH` 解析模型规模，并结合 `VLLM_MAX_MODEL_LEN`、tensor parallel 数自动估算占用率。当前 `models/qwen3-1.7b`、`VLLM_MAX_MODEL_LEN=40960`、两张卡时会选择约 `0.62`。如果你手动设置 `VLLM_GPU_MEMORY_UTILIZATION`，脚本会完全使用你的值。

显存紧张时可降低上下文长度或手动降低 vLLM 显存比例：

```bash
VLLM_CUDA_VISIBLE_DEVICES=0,1 \
OPS_EMBEDDING_CUDA_VISIBLE_DEVICES=2 \
VLLM_MAX_MODEL_LEN=8192 \
VLLM_GPU_MEMORY_UTILIZATION=0.45 \
./scripts/start_all.sh
```

脚本启动前会检查 vLLM 指定 GPU 的空闲显存，不足会提前退出并提示换卡或降低参数。embedding GPU 由后端进程按需加载，不参与 vLLM 显存预占用。

单机多卡默认会设置：

```bash
NCCL_IB_DISABLE=1
NCCL_NET=Socket
```

这是为了避免没有 InfiniBand/RDMA 或 NCCL 网络插件异常的机器在 tensor parallel 初始化时崩溃。普通双 3090 单机部署建议保持默认值。如果你的机器有可用 IB/RDMA，并且确认 NCCL 环境正常，可以在启动前显式覆盖。

## 配置项

常用环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CUDA_VISIBLE_DEVICES` | `0` | 兼容入口：指定 vLLM/base model GPU |
| `VLLM_CUDA_VISIBLE_DEVICES` | 继承 `CUDA_VISIBLE_DEVICES` | 指定 vLLM/base model GPU；例如 `0,1` |
| `OPS_EMBEDDING_CUDA_VISIBLE_DEVICES` | 继承 vLLM GPU | 指定后端 embedding model GPU；例如 `2` |
| `VLLM_GPU_MEMORY_UTILIZATION` | 自动估算 | vLLM 显存利用率；未设置时按模型规模、上下文和 TP 数估算 |
| `OPS_CORS_ORIGINS` | `http://127.0.0.1:5666,http://localhost:5666` | 允许访问后端 API 的前端来源，生产环境不要设为 `*` |
| `OPS_APP_VERSION` | `1.0.0-demo` | 后端健康检查和系统信息展示的交付版本号 |
| `VLLM_MAX_MODEL_LEN` | `40960` | vLLM 最大上下文长度 |
| `VLLM_TENSOR_PARALLEL_SIZE` | vLLM GPU 数量 | vLLM tensor parallel 大小；`VLLM_CUDA_VISIBLE_DEVICES=0,1` 时默认 2 |
| `VLLM_MAX_NUM_SEQS` | 未设置 | vLLM 最大并发序列数；启用多 subagent 并行审阅时可设为 16 |
| `VLLM_MAX_NUM_BATCHED_TOKENS` | 未设置 | vLLM 每批 token 上限；小模型高吞吐可尝试 8192/16384 |
| `NCCL_IB_DISABLE` | `1` | 单机多卡默认关闭 IB，避免 NCCL net plugin 初始化崩溃 |
| `NCCL_NET` | `Socket` | 单机多卡默认使用 socket bootstrap |
| `OPS_MODEL_PATH` | `models/qwen3-1.7b` | 本地模型路径 |
| `OPS_VLLM_MODEL_NAME` | `qwen3-1.7b` | vLLM served model name |
| `OPS_ENABLE_EMBEDDING_RAG` | `true` | 是否启用本地 Qwen3-Embedding RAG；测试或故障时可设为 `false` 使用关键词兜底 |
| `OPS_EMBEDDING_MODEL_PATH` | `models/qwen3-embedding-0.6b` | 本地 Qwen3-Embedding 模型路径 |
| `OPS_EMBEDDING_DEVICE` | `auto` | embedding 模型加载设备；推荐保持 `auto` 并用 `OPS_EMBEDDING_CUDA_VISIBLE_DEVICES` 指定卡 |
| `OPS_EMBEDDING_BATCH_SIZE` | `8` | 知识 chunk embedding 批大小 |
| `OPS_EMBEDDING_MAX_LENGTH` | `8192` | embedding 输入最大 token 长度 |
| `OPS_EMBEDDING_DIMENSION` | `1024` | Qwen3-Embedding 输出维度，可按 MRL 截断为 32-1024 |
| `OPS_EMBEDDING_INDEX_DIR` | `backend/data/vector_index` | 本地 embedding index 持久化目录，知识或模型配置变化时自动重建 |
| `OPS_EMBEDDING_INDEX_BACKEND` | `auto` | 向量索引后端；`auto` 使用 FAISS + numpy 持久化，未安装 FAISS 时降级 numpy；也可设为 `numpy` |
| `OPS_ENABLE_AGENT_LLM` | `false` | 是否启用每个 subagent 的真实 Qwen 结构化审阅；关闭时使用确定性编排 |
| `OPS_AGENT_LLM_PARALLELISM` | `5` | subagent LLM 审阅并行度，默认同时审阅 5 个角色 |
| `OPS_AGENT_LLM_TIMEOUT_SECONDS` | `45` | subagent LLM 审阅调用超时时间 |
| `OPS_ENABLE_INTENT_ROUTER_LLM` | `false` | 是否启用本地 Qwen 对边界输入做 JSON 意图路由建议；默认关闭，规则路由仍会处理常见问候、低信息、无关和高风险输入 |
| `OPS_INTENT_ROUTER_LLM_MIN_CONFIDENCE` | `0.72` | intent router LLM 建议的最低采纳置信度，低于该值时回到确定性路由 |
| `OPS_ENVIRONMENT` | `development` | 运行环境；设为 `production` 时启用安全配置校验 |
| `OPS_JWT_SECRET` | 开发默认值 | JWT 签名密钥；生产环境必须设置为非默认且不少于 32 字符 |
| `OPS_SEED_DEMO_ACCOUNTS` | `true` | 是否写入内置体验账号；生产环境必须设为 `false` |
| `OPS_ADMIN_PASSWORD` | 无 | `scripts/create_admin.py` 非交互模式读取的管理员初始密码 |
| `BACKEND_PORT` | `8010` | 后端端口 |
| `FRONTEND_PORT` | `5666` | 前端端口 |

配置样例见 `.env.example`。生产部署说明见 `docs/deployment.md`。

## 体验账号

以下账号只用于课程演示和本地开发。生产部署请设置 `OPS_ENVIRONMENT=production`、配置强随机 `OPS_JWT_SECRET`，并设置 `OPS_SEED_DEMO_ACCOUNTS=false`。

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 管理员 | `admin` | `admin123` |
| 运维人员 | `ops` | `ops123` |
| 普通用户 | `user` | `user123` |
| 审计员 | `auditor` | `audit123` |

入口建议：

- 普通用户：打开 `/portal`，使用 `user / user123`。
- 工作人员：打开 `/portal` 并选择“工作人员”，使用 `admin / admin123`、`ops / ops123` 或 `auditor / audit123`。

关闭内置体验账号 seed 后，可用离线脚本创建第一个生产管理员：

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

该脚本会检查后端健康、后端就绪、vLLM 就绪、RAG smoke test、知识敏感信息检查和审计 CSV 导出。若刚更新过代码，请先重启后端；详细清单见 `docs/final-acceptance-checklist.md`。

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
