# astrbot_plugin_qfarm

AstrBot + NapCat 的 QQ 农场全量命令插件。  
插件会复用 `qqfarm文档` 的 Node 服务能力，并提供 `qfarm/农场` 双入口命令。

- 作者：`riddle`
- 仓库：`https://github.com/R1ddle1337/astrbot_plugin_qfarm`

## 功能特性

- 全量命令化（不依赖 Web 面板作为主入口）
- 插件内托管 Node 服务（可开关）
- 默认禁用 WebUI，仅保留本机 API（`127.0.0.1`）
- 默认启用图片化输出（正常结果优先渲染图片）
- 每用户单账号绑定
- 用户 + 群双白名单校验
- 群聊 / 私聊都可绑定
- 分级限流（用户冷却 + 全局并发 + 同账号写串行）

## 依赖

1. Python 依赖

```bash
pip install -r requirements.txt
```

2. Node 依赖（必须在 `qqfarm文档` 目录执行）

```bash
cd qqfarm文档
npm install
```

3. 运行环境

- AstrBot
- NapCat（AIOCQHTTP）
- Node.js 18+

## 命令总览

统一入口：

- `/qfarm ...`
- `/农场 ...`

命令清单（33项）：

1. `帮助` `help`
2. `服务 状态|启动|停止|重启`（超管）
3. `账号 查看`
4. `账号 绑定 code <code> [备注名]`
5. `账号 绑定扫码`
6. `账号 取消扫码`
7. `账号 解绑`
8. `账号 启动`
9. `账号 停止`
10. `账号 重连 [code]`
11. `状态`
12. `农田 查看`
13. `农田 操作 all|harvest|clear|plant|upgrade`
14. `好友 列表`
15. `好友 农田 <gid>`
16. `好友 操作 <gid> steal|water|weed|bug|bad`
17. `种子 列表`
18. `背包 查看`
19. `分析 [exp|fert|profit|fert_profit|level]`
20. `自动化 查看`
21. `自动化 设置 <farm|farm_push|land_upgrade|friend|friend_steal|friend_help|friend_bad|task|sell> <on|off>`
22. `自动化 施肥 <both|normal|organic|none>`
23. `设置 策略 <preferred|level|max_exp|max_fert_exp|max_profit|max_fert_profit>`
24. `设置 种子 <seedId>`
25. `设置 间隔 农场 <minSec> <maxSec>`
26. `设置 间隔 好友 <minSec> <maxSec>`
27. `设置 静默 <on|off> <HH:MM> <HH:MM>`
28. `主题 <dark|light>`
29. `日志 [limit] [module=...] [event=...] [keyword=...] [isWarn=0|1]`
30. `账号日志 [limit]`
31. `调试 出售`（超管）
32. `白名单 用户 列表|添加|删除 <uid>`（超管）
33. `白名单 群 列表|添加|删除 <gid>`（超管）

## 权限模型

- 私聊：用户必须在用户白名单中
- 群聊：用户在用户白名单且群在群白名单中
- 超级管理员命令：`服务`、`白名单`、`调试 出售`

超级管理员来源：AstrBot 全局配置中的 `admins_id/admins/admin_ids/superusers`。

## 配置项（_conf_schema.json）

- `managed_mode`: 是否由插件托管 Node 服务（默认 true）
- `node_command`: Node 命令（默认 node）
- `service_host`: API 地址（默认 127.0.0.1）
- `service_port`: API 端口（默认 3000）
- `service_bind_host`: 托管 Node 的监听地址（默认 127.0.0.1）
- `disable_webui`: 是否禁用 Node WebUI（默认 true）
- `service_admin_password`: API 管理密码（默认空，空时自动生成）
- `request_timeout_sec`: 请求超时（默认 15）
- `enable_image_render`: 是否启用图片渲染（默认 true）
- `render_service_url`: 文转图服务地址（默认 `http://172.17.0.1:51234`）
- `render_timeout_sec`: 图片渲染超时（默认 30）
- `render_healthcheck_sec`: 图片服务健康检查超时（默认 3）
- `allowed_user_ids`: 静态用户白名单
- `allowed_group_ids`: 静态群白名单
- `rate_limit_read_sec`: 读命令冷却（默认 1.0）
- `rate_limit_write_sec`: 写命令冷却（默认 2.0）
- `global_concurrency`: 全局并发（默认 20）
- `account_write_serialized`: 同账号写串行（默认 true）

## 持久化文件

插件会在数据目录写入：

- `owner_bindings.json`: 用户 -> 账号绑定
- `whitelist.json`: 动态白名单
- `runtime_secret.json`: 自动生成的服务密码

## 常见问题

1. 命令报连接失败
- 确认 `qqfarm文档` 已执行 `npm install`
- 确认 `node` 命令可用
- 查看 `qfarm 服务 状态`

2. 白名单用户无法在群里使用
- 用户和群都必须在白名单
- 用 `qfarm 白名单 用户 列表`、`qfarm 白名单 群 列表`检查

3. 扫码绑定超时
- 二维码有效期 120 秒
- 重新执行 `qfarm 账号 绑定扫码`

4. 图片化输出没有生效
- 确认 `enable_image_render=true`
- 确认 `render_service_url` 可访问（推荐先测试 `/health`）
- 渲染失败会自动回退纯文本，不影响命令可用

## 搭配 text2img-service

- 推荐渲染服务地址：`http://172.17.0.1:51234`
- Docker 部署与说明见：`docs/文字转图片docker.md`
- qfarm 会调用专用接口 `/api/qfarm` 进行结果渲染
