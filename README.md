# astrbot_plugin_qfarm

AstrBot + NapCat 的 QQ 农场全量命令插件（纯 Python 协议实现）。  
当前版本已彻底移除 Node/npm 运行依赖。

- 作者：`riddle`
- 仓库：`https://github.com/R1ddle1337/astrbot_plugin_qfarm`

## 核心特性

- 全量命令化入口：`/qfarm` 与 `/农场`
- 33 条命令保持兼容
- 每用户单账号绑定
- 用户 + 群双白名单
- 读写分级限流 + 全局并发 + 同账号写串行
- 纯 Python WS + protobuf 后端（无 WebUI）
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

命令集合（33 条）保持与旧版一致：

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

## 配置项（_conf_schema.json）

- `gateway_ws_url`
- `client_version`
- `platform`
- `heartbeat_interval_sec`
- `rpc_timeout_sec`
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
- `runtime_v2.json`：运行态文件
- `whitelist.json`：动态白名单

## 图片渲染联动

默认会尝试调用 `text2img-service`：

- 地址：`http://172.17.0.1:51234`
- 接口：`POST /api/qfarm`

渲染失败会自动回退纯文本，不影响命令执行。

## 故障排查

1. 命令提示 `账号未运行`
- 先执行：`qfarm 账号 启动`
- 再执行：`qfarm 服务 状态`

2. 绑定扫码失败
- 检查容器网络是否可访问 `q.qq.com`
- 重新执行：`qfarm 账号 绑定扫码`

3. 白名单拒绝
- 用户和群必须同时放行（群聊）
- 使用白名单命令检查生效集合

4. 图片不生效
- 检查 `enable_image_render=true`
- 检查 `render_service_url/health`
