# astrbot_plugin_qfarm

AstrBot + NapCat 的 QQ 农场全量命令插件（纯 Python 协议实现）。

- 作者：`riddle`
- 仓库：`https://github.com/R1ddle1337/astrbot_plugin_qfarm`

## 致敬

- 本项目在设计与实现思路上参考并致敬：`https://github.com/Penty-d/qq-farm-bot-ui`

## 核心特性

- 双别名入口：`/qfarm` 与 `/农场`
- 33 条命令全量支持
- 单用户单账号绑定
- 单账号禁止多用户共享绑定（防串号）
- 用户白名单 + 群白名单
- 分级限流（读写分离）+ 全局并发 + 同账号写串行
- 每用户并发护栏（防单用户刷命令拖垮群内体验）
- 运行日志持久化（重载后可追溯）
- 纯 Python WS + protobuf（无 Node/npm 依赖）
- 可选接入 `text2img-service` 的 `/api/qfarm` 图片渲染

## 依赖安装

```bash
pip install -r requirements.txt
```

开发/测试环境建议：

```bash
pip install -r requirements-dev.txt
```

## 运行环境

- AstrBot
- NapCat（AIOCQHTTP）
- Python 3.10+

## 使用流程（推荐）

1. 安装依赖并重载插件
- 在插件目录执行：`pip install -r requirements.txt`
- 重载插件后先发：`/qfarm 帮助`，确认命令可用

2. 配置超级管理员与白名单
- 在插件配置里设置：`super_admin_ids`
- 普通用户使用前，需要加入白名单（超管命令）：
  - `qfarm 白名单 用户 添加 <QQ号>`
  - 群内使用还需：`qfarm 白名单 群 添加 <群号>`

3. 绑定账号
- 推荐扫码：`qfarm 账号 绑定扫码`
- 绑定成功后可查：`qfarm 账号 查看`

4. 启动账号运行时
- 手动启动：`qfarm 账号 启动`
- 查看在线状态：`qfarm 状态`

5. 开启自动化（两种方式）
- 一键全开：`qfarm 自动化 全开`（或 `qfarm 全自动 开`）
- 精细开关示例：
  - `qfarm 自动化 设置 farm on`
  - `qfarm 自动化 设置 task on`
  - `qfarm 自动化 设置 sell on`
  - `qfarm 自动化 施肥 both`

6. 设置自动播种参数（关键）
- 指定种子：`qfarm 设置 种子 <seedId>`
- 缩短农场周期（便于观察）：`qfarm 设置 间隔 农场 2 2`
- 立即种空地：`qfarm 种满`（等价 `qfarm 农田 操作 plant`）

7. 验证自动化是否生效
- 看农田：`qfarm 农田 查看`
- 看运行统计：`qfarm 状态`
- 看运行日志：`qfarm 日志 50`

8. 常用维护
- 停止账号：`qfarm 账号 停止`
- 解绑账号：`qfarm 账号 解绑`
- 一键全关自动化：`qfarm 自动化 全关`

## 命令入口

- `/qfarm ...`
- `/农场 ...`
- `/qfram ...`（常见误拼兼容）

快捷入口：
- `qfarm 登录` 等价 `qfarm 账号 绑定扫码`
- `qfarm 退出登录` 等价 `qfarm 账号 解绑`
- `qfarm 启动` 等价 `qfarm 账号 启动`
- `qfarm 停止` 等价 `qfarm 账号 停止`
- `qfarm 全自动 开|关` 等价 `qfarm 自动化 全开|全关`
- `qfarm 种满` 等价 `qfarm 农田 操作 plant`

## 多用户并发策略（新增）

- 一个 qfarm 账号只能归属一个用户，禁止共享绑定。
- 写操作统一走：用户写冷却 + 全局并发 + 同账号写串行。
- 增加 `per_user_inflight_limit`，限制单用户同时执行中的命令数，避免刷命令影响他人。
- 快捷写命令（`登录/退出登录/启动/停止/重连/种满`）与标准写命令一致纳入写限流。

## 模块补齐说明（Node -> Python）

- 已补齐 `invite.js` 对应 Python 逻辑（`services/domain/invite_service.py`）：
仅 `wx` 平台启用，读取插件数据目录 `share.txt`，调用 `UserService.ReportArkClick`。
- 已补齐配置目录探测：自动优先识别 `qqfarm文档`，并兼容历史目录名差异。
- 已补齐 `farm.js` 的自动解锁语义：`runFarmOperation(all|upgrade)` 会先尝试解锁可解锁土地，再执行升级。
- 已补齐农田状态判定的时间触发语义：`dry_time/weeds_time/insect_time` 到期也会触发浇水/除草/除虫需求。

## 连写命令兼容（新增）

以下连写命令与空格写法等价：

- `农田查看` = `农田 查看`
- `农田操作` = `农田 操作`
- `好友列表` = `好友 列表`
- `账号查看` = `账号 查看`
- `账号启动` = `账号 启动`
- `账号停止` = `账号 停止`
- `账号解绑` = `账号 解绑`
- `账号绑定扫码` = `账号 绑定扫码`
- `账号取消扫码` = `账号 取消扫码`
- `自动化查看` = `自动化 查看`
- `背包查看` = `背包 查看`
- `种子列表` = `种子 列表`
- `服务状态|服务启动|服务停止|服务重启` = 对应空格写法

## 体验优化（新增）

- 未知命令会返回最接近的命令建议（例如提示你可能想输入什么）。
- 关键失败会追加下一步建议（未绑定、未运行、白名单、种子库存不足等）。
- `状态` 输出增加自动化快照和调度说明，便于判断“为何当前没动作”。
- 失败/排障类信息默认文本优先，不强制图片渲染，避免关键信息被图片化吞细节。

## 账号启动重试机制（新增）

- 启动失败后采用有限重试，默认 `3` 次。
- 退避策略默认 `1s -> 2s -> 4s`，最大不超过 `8s`。
- 可通过配置调整重试次数和退避时间。
- 插件启动时会自动拉起全部账号，默认并发数 `5`，单账号失败不会阻断其他账号。

## 运行态字段（新增）

`账号 查看`、`状态`、`服务 状态` 会展示以下运行态信息：

- `runtimeState`：`starting|running|retrying|failed|stopped`
- `lastStartError`：最近一次启动错误
- `lastStartAt`：最近一次启动尝试时间戳
- `lastStartSuccessAt`：最近一次启动成功时间戳
- `startRetryCount`：最近一次启动重试次数

## 主要配置项（_conf_schema.json）

- `gateway_ws_url`
- `client_version`
- `platform`
- `managed_mode`
- `heartbeat_interval_sec`
- `rpc_timeout_sec`
- `start_retry_max_attempts`
- `start_retry_base_delay_sec`
- `start_retry_max_delay_sec`
- `auto_start_concurrency`
- `persist_runtime_logs`
- `runtime_log_max_entries`
- `runtime_log_flush_interval_sec`
- `runtime_log_flush_batch`
- `per_user_inflight_limit`
- `request_timeout_sec`
- `super_admin_ids`
- `allowed_user_ids`
- `allowed_group_ids`
- `rate_limit_read_sec`
- `rate_limit_write_sec`
- `global_concurrency`
- `account_write_serialized`
- `enable_image_render`
- `render_service_url`
- `render_timeout_sec`
- `render_healthcheck_sec`

## 超级管理员配置说明

超级管理员 ID 支持两种来源，最终会合并：

- AstrBot 全局配置：`admins_id` / `admins` / `admin_ids` / `superusers`
- qfarm 插件配置：`super_admin_ids`（推荐）

示例（插件配置）：

```json
{
  "super_admin_ids": ["3615653397"]
}
```

说明：

- 超级管理员可绕过白名单校验
- 超级管理员可执行：`服务`、`白名单`、`调试` 命令
- 修改超管配置后需要重载插件或重启 AstrBot

## 数据文件

插件数据目录会生成：

- `bindings_v2.json`：用户绑定
- `accounts_v2.json`：账号列表
- `settings_v2.json`：自动化与策略配置
- `runtime_v2.json`：运行态与启动状态
- `runtime_logs_v2.json`：运行日志持久化缓存
- `whitelist.json`：动态白名单

隐私与安全建议：

- 不要提交运行态数据文件（`*_v2.json`、`whitelist.json`、`share.txt`、`*cookie*.json`）。
- 已在仓库 `.gitignore` 中加入隐私文件忽略规则，默认避免误提交。

## 图片渲染联动

默认尝试调用 `text2img-service`：

- 地址：`http://172.17.0.1:51234`
- 接口：`POST /api/qfarm`

渲染失败会自动回退纯文本，不影响命令执行。

## 故障排查

1. 提示 `账号未运行`
- 先执行：`qfarm 账号 启动`
- 再执行：`qfarm 状态`
- 查看 `runtimeState`、`lastStartError`、`startRetryCount`

2. 扫码绑定成功但自动启动失败
- 插件会提示具体错误，并建议执行 `qfarm 账号 启动`
- 可在 `qfarm 服务 状态` 查看失败账号摘要

3. 白名单拒绝
- 群聊场景要求：用户在用户白名单 + 群在群白名单
- 可用白名单命令核对生效集合

4. 图片不生效
- 检查 `enable_image_render=true`
- 检查 `render_service_url/health`

5. 绑定时报“禁止共享账号”
- 当前策略为“一个账号仅允许一个用户绑定”
- 如需切换归属，先由原绑定用户执行：`qfarm 账号 解绑`

6. 提示“仍在执行中”
- 命中每用户并发护栏（`per_user_inflight_limit`）
- 等待上一条命令完成，或适当调大该配置

7. 提示 `websocket connect failed: 网关鉴权失败(HTTP 400)`
- 当前绑定的 `code` 很可能已失效
- 重新绑定：`qfarm 账号 绑定 code <code>` 或 `qfarm 账号 绑定扫码`


## QR Security Note

- 扫码登录二维码已改为插件本地生成 PNG（缓存目录：插件 data 下 `qr_cache/`）。
- 纯 Python 主链已移除 `api.qrserver.com` 依赖。

## Version

- Current release: v2.2.6
- 2026-02-22 v2.2.6
- Security: 扫码二维码改为本地生成 PNG，不再依赖第三方二维码服务。
- Test: 新增本地二维码生成与缓存清理测试。
- 2026-02-21 v2.2.5
- Security: 增加运行态与隐私文件忽略规则（`share.txt`、`*_v2.json`、cookie/session 等）。
- Security: `qqfarm文档/share.txt` 已改为忽略，避免未来误上传。
- 2026-02-21 v2.2.4
- Fix: 修复自动播种在“购买成功但返回无明细”场景下的少种问题（库存回写延迟时按成功购买量兜底）。
- Fix: `runtime_log_max_entries` 最小值改为 1，日志裁剪配置按用户设置生效。
- Test: 全量测试通过（68 passed）。
- DevEx: 新增 `requirements-dev.txt` 与 `pytest.ini`，统一异步测试运行环境。
- 2026-02-21 v2.2.3
- Improve: unify command failure template as `前缀 + 错误码 + 引导建议` for easier troubleshooting.
- Improve: normalize backend exception text with `source=<ExceptionType>` locator.
- UX: timeout/auth errors now map to stable guidance path in one format.
- Test: add error-template and api-error-normalization tests.
- 2026-02-21 v2.2.2
- Fix: `request_timeout_sec` now applies to all command-layer API calls.
- Fix: runtime logs switched to batched/interval flush with forced flush on stop.
- Fix: normalize websocket 400 auth failures with explicit rebind guidance.
- Fix: clear historical garbled Chinese texts in runtime/account log messages.
- Improve: remove `print` fallback, warnings now only go through logger.
- Test: add timeout / log-flush-policy / utf8-alias / start-error-classification tests.
- 2026-02-21 v2.2.1
- UX: unknown commands now provide closest-command suggestions.
- UX: key failures now include next-step guidance (bind/start/whitelist/seed-stock).
- UX: status output adds automation snapshot + scheduler explanation text.
- UX: diagnostics/failure messages are forced to text-first (skip image rendering).
- Runtime: farm operation result now includes structured explain fields for no-op diagnosis.
- 2026-02-21 v2.2.0
- Fix: shortcut write commands now hit write-rate-limit and account write-serialization.
- Fix: binding race removed; bind now uses `upsert_account` returned account directly.
- Fix: enforce exclusive account ownership (no shared account binding across users).
- Fix: runtime startup failure now calls `runtime.stop()` for cleanup.
- Fix: rate limiter acquire rollback prevents global semaphore leak on cancellation/errors.
- Improve: runtime logs persisted to `runtime_logs_v2.json` with ring-buffer trimming.
- Improve: add per-user in-flight guard to reduce single-user burst impact.
- Test: add write-classification / exclusive-owner / start-cleanup / cancel-safety / log-persistence / per-user-inflight tests.
- 2026-02-21 v2.1.6
- Docs: add end-to-end quick usage workflow (setup, bind, automation, verify).
- 2026-02-21 v2.1.5
- Feat: add one-click automation commands `qfarm 自动化 全开|全关`.
- Feat: add shortcut `qfarm 全自动 [开|关]` (default: 开).
- Improve: one-click toggle also syncs fertilizer mode (`开 -> both`, `关 -> none`).
- Test: add one-click automation command routing and payload regression tests.
- 2026-02-21 v2.1.4
- 2026-02-21 v2.1.4
- Improve: planting failures now include per-land error samples (`items=...; map=...`) to avoid empty diagnostics.
- Improve: auto-plant adds seed-stock precheck before final Plant call and returns explicit guidance when stock is zero.
- Test: add non-empty plant error text regression and failure-sample rendering regression tests.
- 2026-02-21 v2.1.3
- Fix: align planting RPC with Node semantics (`PlantRequest.items` first, map fallback for compatibility).
- Fix: when all lands fail to plant, command now surfaces backend error text instead of generic message.
- Test: add plant-protocol payload regression and plant-failure reason regression tests.
- 2026-02-21 v2.1.2
- Feat: add quick command `qfarm 种满` for immediate empty-land planting.
- Improve: `农田 操作` now returns real execution summary and planting result instead of generic “已提交”.
- Improve: when planting fails, response includes explicit reason (e.g. no seed stock / insufficient gold).
- Test: add farm-operation response and quick-plant command regression tests.
- 2026-02-21 v2.1.1
- Feat: align farm runtime with Node unlock flow (`all|upgrade` now unlocks `unlockable` lands before upgrades).
- Fix: align land-analyze flags with Node phase-time semantics (`dry_time/weeds_time/insect_time` triggers).
- Improve: land detail now includes `seedId/seedImage/couldUnlock/couldUpgrade/maxLevel/landsLevel/landSize`.
- Test: add phase-time trigger regression and runtime unlock-flow regression.
- 2026-02-21 v2.1.0
- Feat: add Python InviteService parity (`share.txt` + `ReportArkClick`, wx-only).
- Fix: robust qfarm docs path resolution to avoid garbled-dir config load failures.
- Test: add docs-root resolution and invite-service regression tests.
- 2026-02-21 v2.0.9
- Fix: farm clear/harvest/upgrade now isolate step failures so one RPC error does not abort the whole cycle.
- Fix: seed list supports local-config fallback when shop RPC is unavailable.
- Feat: command usability shortcuts (`登录/退出登录/启动/停止`) and typo alias (`qfram`).
- Test: add fallback seed listing and farm-cycle failure-isolation regression tests.
- 2026-02-21 v2.0.8
- Fix: auto-plant now checks bag seed stock and caps buy count by current gold affordability.
- Fix: when buy fails after stock check, planting falls back to available seed stock instead of aborting all targets.
- Add: runtime seed planning debug logs (`seed_plan`, `seed_stock_check_failed`, `seed_unavailable_runtime`).
- 2026-02-21 v2.0.7
- Fix: align auto-plant with Node semantics, harvested lands now go through remove->plant flow.
- Fix: if seed purchase fails, runtime still attempts planting with existing inventory seeds.
- 2026-02-21 v2.0.6
- Docs: add tribute note for `https://github.com/Penty-d/qq-farm-bot-ui`.
- 2026-02-21 v2.0.5
- Fix: harden command event resolution to handle AstrBot callback arg-order variance.
- Fix: add guarded result builders to prevent `plain_result/image_result` attribute crashes.
- 2026-02-21 v2.0.4
- Fix: command entry now resolves AstrBot event object from mixed callback argument orders.
- Fix: avoid `QFarmPlugin has no attribute plain_result` when framework passes extra positional args.
- 2026-02-21 v2.0.3
- Fix: auto-plant now treats harvested lands as empty targets instead of dead targets.
- Fix: when `remove_plant` fails on dead land cleanup, planting continues instead of aborting.
- Add: regression test for remove-failure-continue-plant behavior.
- 2026-02-21 v2.0.2
- Fix: command handler now accepts AstrBot extra positional args (avoid TypeError).
- Fix: websocket 400 invalid response status is classified as non-retryable start error.
- 2026-02-21 v2.0.1
- Fix: mature lands are harvestable even when stealable=false.
- Add: farm analyze/harvest debug logs for troubleshooting.
- Add: regression tests for mature-harvest and runtime farm operation.
