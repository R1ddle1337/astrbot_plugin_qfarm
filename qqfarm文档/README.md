# QQ 农场多账号挂机 + Web 面板

这是一个基于 Node.js 的 QQ 农场自动化项目，支持多账号运行、Web 控制面板、自动农场/好友/任务流程和数据分析。

## 功能

### 多账号
- 账号新增、编辑、删除、启动、停止
- 扫码登录（QQ）与手动输入 Code
- 账号被踢下线自动删除
- 账号连续离线超时自动删除（默认 5 分钟）
- 账号操作日志独立展示

### 自动化能力
- 农场：收获、种植、浇水、除草、除虫、铲除、土地升级
- 仓库：收获后自动出售果实（受开关控制）
- 好友：自动偷菜/帮忙/捣乱（子开关）
- 任务：自动检查并领取（并入统一调度）
- 推送触发巡田（LandsNotify）开关
- 好友静默时段（如 23:00-07:00）

### 面板
- 概览/农场/背包/好友/分析/账号/设置页面
- 日志筛选：账号、模块、事件、级别、关键词、时间范围
- 主题切换（深色/浅色）

### 分析页
支持以下排序：
- 按经验效率
- 按普通肥经验效率
- 按净利润效率
- 按普通肥净利润效率
- 按等级要求

## 环境要求
- 源码运行：Node.js 18+
- 二进制发布运行：无需安装 Node.js

## 安装与启动

### Windows

1. 安装 Node.js（建议 18+）
- 到官网下载安装包：`https://nodejs.org/`
- 安装完成后在 PowerShell 验证：

```powershell
node -v
npm -v
```

2. 进入项目目录并安装依赖

```powershell
cd D:\Projects\qq-farm-bot-ui
npm install
```

3. 启动项目

```powershell
node client.js
```

4. （可选）设置管理密码后启动

```powershell
$env:ADMIN_PASSWORD="你的强密码"
node client.js
```

### Linux（Ubuntu/Debian 示例）

1. 安装 Node.js 18+

```bash
sudo apt update
sudo apt install -y curl
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install -y nodejs
node -v
npm -v
```

2. 进入项目目录并安装依赖

```bash
cd /path/to/qq-farm-bot-ui
npm install
```

3. 启动项目

```bash
node client.js
```

4. 设置管理密码后启动

```bash
ADMIN_PASSWORD='你的强密码' node client.js
```

默认面板端口为 `3000`：
- 本机访问：`http://localhost:3000`
- 局域网访问：`http://<你的IP>:3000`

## 发布为免安装版本（Windows/Linux/macOS）

### 构建环境（开发者机器）

```bash
npm install
```

```bash
npm run build:release
```

构建产物输出在 `dist/` 目录。

### 产物说明
- Windows: `dist/farm-win.exe`
- Linux: `dist/farm-linux`
- macOS Intel: `dist/farm-macos-x64`
- macOS Apple Silicon: `dist/farm-macos-arm64`

### 用户运行方式（无需 Node.js）

- Windows: 双击 exe 或在终端运行 `.\farm-win.exe`
- Linux: `chmod +x ./farm-linux && ./farm-linux`
- macOS: `chmod +x ./farm-macos-arm64 && ./farm-macos-arm64`（或 x64 版本）

程序会在可执行文件同级目录自动创建 `data/` 并写入配置与账号数据：
- `data/store.json`
- `data/accounts.json`

## 登录与安全
- 面板首次访问需要登录
- 默认管理密码：`admin`

## 目录结构

```text
client.js                    # 主进程：worker 管理、日志聚合、配置广播
src/admin.js                 # HTTP API + 面板静态资源
src/worker.js                # 单账号 worker（统一调度 + 状态同步）
src/farm.js                  # 农场逻辑
src/friend.js                # 好友逻辑
src/task.js                  # 任务逻辑
src/warehouse.js             # 背包与出售逻辑
src/store.js                 # 全局配置与账号持久化
data/store.json              # 运行配置持久化
data/accounts.json           # 账号数据持久化
panel/index.html             # 面板页面结构
panel/style.css              # 面板样式
panel/js/core.js             # 前端基础状态/API/工具
panel/js/polling-accounts.js # 轮询、账号与日志主流程
panel/js/pages.js            # 农场/好友/分析/背包页面逻辑
panel/js/modal-accounts.js   # 添加账号弹窗/扫码登录逻辑
panel/js/init.js             # 前端初始化与事件绑定
```

## 特别感谢

- 核心功能实现：[linguo2625469/qq-farm-bot](https://github.com/linguo2625469/qq-farm-bot)
- 扫码登录功能实现：[lkeme/QRLib](https://github.com/lkeme/QRLib)

## 免责声明

本项目仅供学习和研究用途。使用本工具可能违反游戏服务条款，由此产生的一切后果由使用者自行承担。
