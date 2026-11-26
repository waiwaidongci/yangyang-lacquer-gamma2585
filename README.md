# 雕版补漆干燥工时估算

小型传统机器学习后端。带对数链接的 Gamma 回归及按日留出。提供台账校验与导入、参数校验与同步计算、结果复核和 CSV 导出。完整业务口径见 [BUSINESS.md](BUSINESS.md)。

## 本地运行

```sh
docker compose up -d --build --wait
curl -fsS http://127.0.0.1:19344/api/health
curl -fsS http://127.0.0.1:19344/api/spec
```

API 默认监听 `127.0.0.1:19344`。接口文档在 `/api/docs`，机器可读规范在 `/api/openapi.json`。数据保存在 SQLite 具名卷中，`docker compose down` 后仍可使用。服务首次启动会装载模拟业务数据。

| 本地演示账号 | 口令 | 权限 |
|---|---|---|
| `analyst@demo.local` | `Demo-2611!` | 导入数据、运行计算、核对结果 |
| `admin@demo.local` | `Demo-2611!` | 分析员权限及结果复核 |
| `viewer@demo.local` | `Demo-2611!` | 查阅来源、已批准的结果并导出 CSV |

## API 工作流

1. `POST /api/auth/login` 接收带 `email`、`password` 的 JSON。后续业务请求通过 `Authorization: Bearer <access_token>` 鉴权。
2. `GET /api/spec` 查看 CSV 列、参数和结果口径。`GET /api/datasets` 查找演示台账，或用 `POST /api/datasets` 提交带 `name`、`csv` 的 JSON。
3. `GET /api/spec` 查看 CSV 列、参数和结果口径。`GET /api/datasets` 查找演示台账，或用 `POST /api/datasets` 提交带 `name`、`csv` 的 JSON。
4. 导入时若已完工记录的 `dry_hours` 超出数值规则，不会退回整份台账：原单元格保存在行内 `dry_hours_raw`/`original_dry_hours`，并在 `dry_hours_reason`/`invalid_reason` 中说明原因；该记录不参与拟合或评分。
5. `POST /api/datasets/{dataset_id}/corrections` 提交补正，正文可用 `corrections`、`changes` 或 `items` 列表，每条支持 `id`/`order_id`、`dry_hours`/`corrected_hours`、`reason`/`note`。不同工单的有效补正分别入账；同一工单出现不同有效数值时标记为 `disputed` 并暂缓使用，所有补正均保留。可通过 `Idempotency-Key` 请求头、`idempotency_key` 字段或条目 `correction_id` 安全重试。`GET .../corrections` 查看全部补正。
6. `POST /api/runs` 提交 `dataset_id`、`label` 和可选的 `settings`。响应包含运行 ID 及最终状态。`GET /api/runs/{id}` 返回业务指标、训练/留出样本清单、表格或失败原因。每次运行冻结当时结果，后续补正只影响新运行，旧估时仍按原 JSON/表格查看。
7. 负责人调用 `POST /api/runs/{id}/review`，提交 `decision`（`approved` 或 `rejected`）和 `note`。批准后可通过 `GET /api/runs/{id}/export/{table_key}` 下载业务表。表名和字段从运行结果取得。

## 代码与配置

- `backend/src/domain` 实现数据规范、模拟样本和业务算法。
- `backend/src/services` 与 `backend/src/models` 实现台账、计算、用户权限和复核记录。
- `backend/src/routes`、`schemas` 和 `controllers` 提供 FastAPI 接口与输入输出约束。
- `backend/migrations` 和 `backend/scripts` 提供 SQLite 初始化和可重复执行的演示数据装载。

可在 `.env` 中配置 `BACKEND_PORT`、`SEED_DEMO` 和 `JWT_SECRET`。演示账号、默认签名密钥及模拟台账用于本地验证。启用演示数据也可执行 `docker compose exec -T backend python -m scripts.seed_demo`。
