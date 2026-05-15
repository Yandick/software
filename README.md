# 运维数字员工系统

本项目交付一个可运行的“AI + RAG 私有知识库 + 运维申告门户”课程项目，覆盖：本地 Qwen3 大模型、知识库自助问答、低置信度转人工、在线记录处理与回访、处理结果沉淀知识库、运维账号新增/冻结/解冻/修改/查询、JWT + RBAC 与审计统计。

## 目录说明

- `backend/`：FastAPI 后端，SQLite 数据库，JWT + RBAC 权限控制。
- `backend/app/data/knowledge_seed.json`：内置运维 FAQ/Runbook 种子知识库，首次启动自动导入。
- `backend/data/app.db`：后端启动后自动生成的 SQLite 数据库。
- `frontend/`：Vue Vben Admin 前端模板，当前使用 `apps/web-antd`。
- `models/qwen3-1.7b`：本地 Qwen3-1.7B 模型目录。
- `model-use.md`：Qwen3-1.7B 官方使用说明摘录，包含 think/no-think、vLLM/SGLang 和工具调用建议。
- `agent.md`：项目开发约定和后续开发知识沉淀。
- `docs/reference-repos.md`：本地参考仓库记录，包含 Vue Vben Admin 官方仓库路径。
- `docs/auth-roadmap.md`：后续认证能力路线图。

## 知识库数据怎么获得

当前项目不是空壳：`backend/app/data/knowledge_seed.json` 已内置 10 条运维知识，覆盖账号冻结、密码重置、VPN、邮箱、权限申请、系统慢、磁盘告警、数据库连接失败、转人工标准、知识沉淀流程。后端首次启动会自动写入 SQLite。

后续真实知识库建议从以下来源持续补充：

1. 运维 FAQ：高频问题、标准答复、服务入口说明。
2. Runbook：磁盘、数据库、VPN、邮件、应用系统慢等标准排查步骤。
3. 制度流程：账号、权限、审批、审计、安全边界。
4. 历史工单：脱敏后整理为“现象-原因-处理-验证-注意事项”。
5. 人工处理闭环：在线记录回访确认解决后，系统可自动把处理结果沉淀为处理案例知识。

## Qwen3-1.7B 推理方式选择

根据 `model-use.md`，项目采用“vLLM OpenAI-compatible 服务优先，Transformers 进程内兜底，纯检索兜底可测试”的方式：

- 推荐部署方式：`vllm>=0.8.5`，启动 OpenAI-compatible API，并启用 reasoning parser。
- 项目默认推理后端：`OPS_INFERENCE_BACKEND=auto`，先尝试 `http://127.0.0.1:8000/v1/chat/completions`，不可用时尝试 Transformers 本地加载。
- RAG 问答默认使用 non-thinking：效率更高，适合常规运维 FAQ；复杂摘要/分析可在请求中传 `enable_thinking=true` 或设置 `OPS_ENABLE_THINKING=true`。
- 采样参数遵循官方建议：thinking 使用 `temperature=0.6, top_p=0.95, top_k=20`；non-thinking 使用 `temperature=0.7, top_p=0.8, top_k=20`；不使用 greedy decoding。
- 工具调用：Qwen3 推荐 Qwen-Agent，但本项目现阶段不让模型直接调用账号变更工具；账号新增、冻结、解冻、修改必须走后端 RBAC 接口并写审计日志。

启动 vLLM：

```bash
cd /data/yhwu/software
backend/app/scripts/start_vllm_qwen3.sh
```

等 vLLM 监听 `127.0.0.1:8000` 后启动后端。

## 后端启动

```bash
cd /data/yhwu/software
conda run -n software python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8010
```

健康检查：

```bash
curl http://127.0.0.1:8010/api/health
```

如果只想测试业务接口、不加载模型：

```bash
OPS_INFERENCE_BACKEND=retrieval conda run -n software python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8010
```

## 前端启动

```bash
cd /data/yhwu/software/frontend
pnpm install
pnpm --filter ops-employee-frontend dev
```

默认访问：

```text
http://127.0.0.1:5666
```

前端 API 默认是 `/api`，开发时可用 Vite 代理或反向代理到后端 `http://127.0.0.1:8010/api`。

## 演示账号

当前系统只启用账号密码登录。微信、QQ、Github、Google、手机号验证码、扫码登录、创建账号和忘记密码流程尚未接入，前端登录页已隐藏这些未实现入口；这些能力已标记为后续现实部署需要补齐的认证模块，详见 `docs/auth-roadmap.md`。

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 管理员 | `admin` | `admin123` |
| 运维人员 | `ops` | `ops123` |
| 普通用户 | `user` | `user123` |
| 审计员 | `auditor` | `audit123` |

## 依赖检查

`requirement.txt` 是当前环境完整冻结列表；项目最小依赖见 `requirements-minimal.txt`。检查当前环境是否满足：

```bash
cd /data/yhwu/software
python scripts/check_dependencies.py
```

当前已验证必要依赖完整：FastAPI、Uvicorn、pydantic-settings、PyJWT、httpx、scikit-learn、Transformers、Torch、vLLM 均可导入。可选增强依赖 `qwen-agent` 和 `sentence-transformers` 当前未安装；现阶段不阻塞运行，后续如实现工具调用 Agent 或向量嵌入检索再安装。

## 一键启动

推荐直接使用一键脚本，它会按顺序启动 vLLM、后端和前端，日志写入 `logs/`，PID 写入 `.run/`，按 `Ctrl+C` 会自动停止本脚本启动的服务：

```bash
cd /data/yhwu/software
./scripts/start_all.sh
```

常用模式：

```bash
# 快速测试业务接口：不启动大模型、不启动前端
./scripts/start_all.sh --no-llm --no-frontend

# 不启动大模型，后端使用知识库检索兜底，仍启动前端
./scripts/start_all.sh --no-llm

# 首次启动前端依赖未安装时
./scripts/start_all.sh --install-frontend

# vLLM 启动失败时不中断，后端切换 retrieval 兜底
./scripts/start_all.sh --allow-no-llm

# 手动停止后台进程
./scripts/stop_all.sh
```

可通过环境变量调整端口和环境：

```bash
OPS_CONDA_ENV=stream BACKEND_PORT=8010 FRONTEND_PORT=5666 ./scripts/start_all.sh
```

### GPU 选择与 CUDA_VISIBLE_DEVICES

本项目启动大模型时需要明确设置可见 GPU，避免 vLLM 默认占用错误显卡或和其他任务冲突。一键脚本会在未设置时默认使用 GPU 0：

```bash
./scripts/start_all.sh
```

等价于：

```bash
CUDA_VISIBLE_DEVICES=0 ./scripts/start_all.sh
```

如果要显式指定其他 GPU：

```bash
# 使用 1 号卡
CUDA_VISIBLE_DEVICES=1 ./scripts/start_all.sh

# 或使用脚本参数
./scripts/start_all.sh --cuda-devices 1

# 多卡可见，后续如开启 tensor parallel 再配合 vLLM 参数扩展
CUDA_VISIBLE_DEVICES=0,1 ./scripts/start_all.sh
```

注意：当前脚本默认只暴露 GPU 0，适合 Qwen3-1.7B 单卡推理。服务器多人共用时，启动前建议先执行 `nvidia-smi` 查看空闲显卡，再设置 `CUDA_VISIBLE_DEVICES`。

### vLLM 版本兼容说明

当前环境的 vLLM 参数中没有 `--enable-reasoning`，但支持 `--reasoning-parser` 和 `chat_template_kwargs`。因此脚本实际使用：

```bash
python -m vllm.entrypoints.openai.api_server \
  --model models/qwen3-1.7b \
  --served-model-name qwen3-1.7b \
  --host 127.0.0.1 \
  --port 8000 \
  --reasoning-parser deepseek_r1
```

后端请求时会传 `chat_template_kwargs: {"enable_thinking": false}`，常规 RAG 问答默认 non-thinking；需要复杂推理时可在问答接口传 `enable_thinking=true`。

脚本启动 vLLM 前会检查所选第一张 GPU 的空闲显存，并打印类似信息：

```text
GPU 0 memory: total=24576MiB used=1848MiB free=22277MiB; vLLM target=22118MiB
```

如果提示显存不足，请换空闲 GPU，例如：

```bash
CUDA_VISIBLE_DEVICES=6 ./scripts/start_all.sh
```

或者在显存紧张时降低 vLLM 预留比例和上下文长度：

```bash
VLLM_MAX_MODEL_LEN=8192 VLLM_GPU_MEMORY_UTILIZATION=0.25 CUDA_VISIBLE_DEVICES=0 ./scripts/start_all.sh
```
