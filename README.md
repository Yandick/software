# 运维数字员工系统

面向企业 IT 运维场景的本地化数字员工系统，覆盖知识检索问答、在线记录流转、人工处理、知识沉淀、账号管理、权限控制与审计统计。模型推理链路基于本地 Qwen3/vLLM，知识检索链路基于 Qwen3-Embedding、FAISS 和本地持久化索引。

## 核心能力

- 本地模型服务：通过 vLLM 部署 Qwen3，提供 OpenAI-compatible 推理接口。
- 私有知识检索：基于 Qwen3-Embedding、FAISS/numpy 持久化索引和关键词重排实现 hybrid RAG。
- 用户服务门户：支持运维咨询、在线记录提交、处理进度查看和用户反馈。
- 工作人员管理台：支持记录受理、处理填报、知识候选生成、知识审核和账号管理。
- 多角色编排：按 intent router、supervisor、risk guardian、ops employee、knowledge curator、evaluator 组织运维任务链路。
- 权限与审计：基于 JWT/RBAC 控制访问边界，保留问答、处理、账号和知识变更审计记录。

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
├── scripts/start_all.sh      # 启动脚本
├── scripts/stop_all.sh       # 停止脚本
├── scripts/package_check.py   # 离线检查脚本
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

首次运行可交给启动脚本安装，也可以手动安装：

```bash
cd frontend
pnpm install
cd ..
```

## 启动

统一启动入口：

```bash
./scripts/start_all.sh --base-cuda-devices 0,1 --embedding-cuda-devices 2
```

这个命令会依次启动 vLLM、后端和前端。启动前会停止本项目此前启动的进程，释放对应端口，并按模型规模和 GPU 数量自动估算 vLLM 显存占用率。默认启用真实 subagent LLM 审阅。

常用可选参数：

```bash
./scripts/start_all.sh --base-cuda-devices 0,1 --embedding-cuda-devices 2 --install-frontend
./scripts/start_all.sh --base-cuda-devices 0,1 --embedding-cuda-devices 2 --no-frontend
```

默认端口：

- 前端：`FRONTEND_PORT=5666`
- 后端：`BACKEND_PORT=8010`
- vLLM：`OPS_VLLM_PORT=8000`

停止服务：

```bash
./scripts/stop_all.sh
```

启动脚本统一写入 `.run/frontend.pid`、`.run/backend.pid`、`.run/vllm.pid`；停止脚本按 `FRONTEND_PORT`、`BACKEND_PORT`、`OPS_VLLM_PORT` 检查和释放本项目占用的端口。

前端入口按角色组织：

- `/` 和 `/portal` 进入用户服务门户。
- `/portal` 提供“用户 / 工作人员”两种身份入口；普通用户登录后只展示数字员工咨询、在线记录提交和本人处理进度。
- 运维、管理员和审计员在 `/portal` 选择“工作人员”身份后进入 `/ops/*` 管理界面。
- 登录后的用户门户和工作人员管理台右上角都提供一致位置的“门户首页 / 切换身份”操作。

## 请求路由与风险控制

问答入口先经过 `intent_router`，再决定是否进入 RAG、subagent 编排和受控处理流程：

- 问候、感谢、低信息和非运维问题直接返回范围提示，不触发 RAG 或工单草稿。
- 运维支持类问题进入 FAISS/Qwen3-Embedding hybrid RAG，并由多角色链路完成判断、回答和知识沉淀建议。
- 账号、权限、生产、数据库、删除、提权、绕过审批等高风险请求进入受控流程，只提供流程说明和人工协同入口。
- 默认使用确定性路由和安全门控；`OPS_ENABLE_INTENT_ROUTER_LLM=true` 时，本地 Qwen 只提供 JSON 路由建议，不覆盖后端 RBAC、风险规则和审计要求。

处理链路：

```text
user input
  -> intent_router: scope / low-info / out-of-scope / controlled / ops-support
  -> ops-support only: FAISS + Qwen3-Embedding RAG
  -> Qwen base model: grounded answer or structured subagent review
  -> deterministic risk gate / RBAC / audit
```

## GPU 与显存参数

启动时显式区分 base model 和 embedding model 使用的 GPU：

```bash
./scripts/start_all.sh --base-cuda-devices 0,1 --embedding-cuda-devices 2
```

上面表示：

- Qwen base model 由 vLLM 启动在 GPU 0、1，并自动设置 `VLLM_TENSOR_PARALLEL_SIZE=2`。
- Qwen3-Embedding 后端进程只看到 GPU 2；`OPS_EMBEDDING_DEVICE=auto` 会加载到这张卡上。

`VLLM_GPU_MEMORY_UTILIZATION` 未设置时，启动脚本会从 `OPS_MODEL_PATH` 解析模型规模，并结合 `VLLM_MAX_MODEL_LEN`、tensor parallel 数自动估算占用率。当前 `models/qwen3-1.7b`、`VLLM_MAX_MODEL_LEN=40960`、两张卡时会选择约 `0.62`。如果你手动设置 `VLLM_GPU_MEMORY_UTILIZATION`，脚本会完全使用你的值。

显存紧张时可降低上下文长度或手动降低 vLLM 显存比例：

```bash
VLLM_MAX_MODEL_LEN=8192 \
VLLM_GPU_MEMORY_UTILIZATION=0.45 \
./scripts/start_all.sh --base-cuda-devices 0,1 --embedding-cuda-devices 2
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
| `OPS_BASE_CUDA_VISIBLE_DEVICES` | `0` | 指定 vLLM/base model GPU；例如 `0,1` |
| `OPS_EMBEDDING_CUDA_VISIBLE_DEVICES` | 继承 vLLM GPU | 指定后端 embedding model GPU；例如 `2` |
| `VLLM_GPU_MEMORY_UTILIZATION` | 自动估算 | vLLM 显存利用率；未设置时按模型规模、上下文和 TP 数估算 |
| `OPS_CORS_ORIGINS` | 开发默认前端来源 | 允许访问后端 API 的前端来源，生产环境必须设置为实际域名且不要设为 `*` |
| `OPS_APP_VERSION` | `1.0.0` | 后端健康检查和系统信息展示版本号 |
| `VLLM_MAX_MODEL_LEN` | `40960` | vLLM 最大上下文长度 |
| `VLLM_TENSOR_PARALLEL_SIZE` | vLLM GPU 数量 | vLLM tensor parallel 大小；base GPU 为 `0,1` 时默认 2 |
| `VLLM_MAX_NUM_SEQS` | 自动估算 | vLLM 最大并发序列数；按 base GPU 数量设置保守默认值 |
| `VLLM_MAX_NUM_BATCHED_TOKENS` | 自动估算 | vLLM 每批 token 上限；按 base GPU 数量设置保守默认值 |
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
| `OPS_ENABLE_AGENT_LLM` | `true` | 是否启用每个 subagent 的真实 Qwen 结构化审阅 |
| `OPS_AGENT_LLM_PARALLELISM` | `5` | subagent LLM 审阅并行度，默认同时审阅 5 个角色 |
| `OPS_AGENT_LLM_TIMEOUT_SECONDS` | `45` | subagent LLM 审阅调用超时时间 |
| `OPS_ENABLE_INTENT_ROUTER_LLM` | `false` | 是否启用本地 Qwen 对边界输入做 JSON 意图路由建议；默认关闭，规则路由仍会处理常见问候、低信息、无关和高风险输入 |
| `OPS_INTENT_ROUTER_LLM_MIN_CONFIDENCE` | `0.72` | intent router LLM 建议的最低采纳置信度，低于该值时回到确定性路由 |
| `OPS_ENVIRONMENT` | `development` | 运行环境；设为 `production` 时启用安全配置校验 |
| `OPS_JWT_SECRET` | 开发默认值 | JWT 签名密钥；生产环境必须设置为非默认且不少于 32 字符 |
| `OPS_SEED_DEMO_ACCOUNTS` | `true` | 是否写入内置样例账号；生产环境必须设为 `false` |
| `OPS_ADMIN_PASSWORD` | 无 | `scripts/create_admin.py` 非交互模式读取的管理员初始密码 |
| `BACKEND_PORT` | `8010` | 后端端口 |
| `FRONTEND_PORT` | `5666` | 前端端口 |

配置样例见 `.env.example`。启动前按实际机器设置 base model 和 embedding model 使用的显卡。

## 内置账号

以下账号仅用于本地验证。生产部署请设置 `OPS_ENVIRONMENT=production`、配置强随机 `OPS_JWT_SECRET`，并设置 `OPS_SEED_DEMO_ACCOUNTS=false`。

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 管理员 | `admin` | `admin123` |
| 运维人员 | `ops` | `ops123` |
| 普通用户 | `user` | `user123` |
| 审计员 | `auditor` | `audit123` |

角色入口：

- 普通用户：进入 `/portal`，使用 `user / user123`。
- 工作人员：进入 `/portal` 并选择“工作人员”，使用 `admin / admin123`、`ops / ops123` 或 `auditor / audit123`。

关闭内置样例账号 seed 后，可用离线脚本创建第一个生产管理员：

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

## 系统健康与检查

离线检查不需要启动后端或 GPU：

```bash
python scripts/package_check.py
```

运行中的服务提供三个系统检查入口：

- `/api/health`：公开进程存活检查，返回应用名和版本号。
- `/api/ready`：公开后端就绪检查，覆盖配置和 SQLite schema。
- `/api/ready?include_llm=true&require_llm=true`：完整链路就绪检查，额外要求 vLLM 可用。
- `/api/system/info`：登录后查看系统版本、前端包、数据库类型、模型路径和功能开关，不返回密钥。

## 数据库初始化

首次部署或版本升级后执行：

```bash
alembic upgrade head
```

密码使用 Argon2id 存储。

## 提交检查

提交前先执行离线检查。服务启动后再执行运行检查。

```bash
python scripts/package_check.py
python scripts/run_acceptance.py
```

`package_check.py` 不要求服务运行；`run_acceptance.py` 会检查后端健康、后端就绪、vLLM 就绪、RAG smoke test、知识敏感信息检查和审计 CSV 导出。

## 文件说明

- `scripts/start_all.sh`：统一启动入口，部署人只需要指定 base model 和 embedding model 使用的显卡。
- `scripts/stop_all.sh`：停止本项目启动的前端、后端和 vLLM 进程。
- `scripts/package_check.py`：离线检查，提交或部署前先运行。
- `scripts/run_acceptance.py`：服务启动后的运行检查。
- `.env.example`：公开配置样例；真实 `.env`、模型权重、数据库、日志和内部任务文档不进入 Git。

## Agent 扩展边界

当前主链路为 RAG + vLLM + 受控 subagent 编排。后续扩展 ReAct 或 qwen-agent 工作流时，工具调用仍必须经过后端 API、RBAC 和审计日志，不允许模型直接执行账号冻结、解冻、权限变更、生产变更等高风险操作。
