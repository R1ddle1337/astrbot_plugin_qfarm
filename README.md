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
- 用户白名单 + 群白名单
- 分级限流（读写分离）+ 全局并发 + 同账号写串行
- 纯 Python WS + protobuf（无 Node/npm 依赖）
- 可选接入 `text2img-service` 的 `/api/qfarm` 图片渲染

## 依赖安装

```bash
pip install -r requirements.txt
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
- `whitelist.json`：动态白名单

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


## Version

- Current release: v2.1.6
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
