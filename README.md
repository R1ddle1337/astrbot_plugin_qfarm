# astrbot_plugin_qfarm

AstrBot + NapCat 的 QQ 农场全量命令插件（纯 Python 协议实现）。

- 作者：`riddle`
- 仓库：`https://github.com/R1ddle1337/astrbot_plugin_qfarm`

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

## 命令入口

- `/qfarm ...`
- `/农场 ...`

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
