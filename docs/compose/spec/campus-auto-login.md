---
feature: campus-auto-login
status: designed
updated: 2026-09-12
branch: feat/campus-auto-login
commits:
---

# 校园网自动登录监控

## Report

## [S1] Problem
Windows 本机在校园网环境下，断网或认证失效后需要人工打开 `http://192.168.200.2`，勾选「校园电信」并输入账号密码。需要一个后台程序每 10 秒探测公网连通性，异常时自动用预设账号登录 Dr.COM 认证系统，恢复上网。

## [S2] Design

### 风格与交付形态
- 交付：Python 单文件脚本 + PyInstaller 单文件 Windows exe
- 运行：控制台循环常驻；账号密码先写在脚本顶部常量
- 约束：**绝不调用注销接口**，不主动踢下线当前会话

### 网络检测
- 周期：`CHECK_INTERVAL_SEC = 10`
- 探测 URL：`http://www.msftconnecttest.com/connecttest.txt`
- 成功条件：HTTP 200 且响应体包含 `Microsoft Connect Test`
- 连续失败达到 `FAIL_THRESHOLD`（默认 2 次）才触发登录，避免瞬时抖动
- 登录后进入冷却 `LOGIN_COOLDOWN_SEC`（默认 30s），冷却内不重复登录

### 认证系统（Dr.COM 哆点，实测）
| 项 | 值 |
|---|---|
| 门户 | `http://192.168.200.2` |
| 认证主机 | `http://192.168.200.2:801` |
| eportal | `http://192.168.200.2:801/eportal/` |
| 账号字段 | `DDDDD` |
| 密码字段 | `upass` |
| 运营商单选 | `校园电信` → suffix `@dx` |
| 备选 suffix | `@telecom`（当前在线会话显示） |
| 成功页标记 | `Dr.COMWebLoginID_3.htm` |
| 失败页标记 | `Dr.COMWebLoginID_2.htm` |
| loginMethod | 1（Portal 协议，config.js ipPageAry[2]） |

### 登录策略（双协议 + 双后缀）
按固定顺序尝试，任一步骤成功即停止：

1. **ACSetting POST**（优先，结构简单）
   - `POST http://192.168.200.2:801/eportal/?c=ACSetting&a=Login`
   - body: `DDDDD=<user><suffix>&upass=<pass>&0MKKey=123456`
   - 成功：响应含 `Dr.COMWebLoginID_3.htm` 或不含 `Dr.COMWebLoginID_2.htm` 且能探测到公网恢复
   - suffix 顺序：`@dx` → `@telecom`

2. **Portal JSONP 协议**（回退）
   - `GET http://192.168.200.2:801/eportal/?c=Portal&a=login`
   - query:
     - `login_method=1`
     - `user_account=,0,<user><suffix>`（PC 前缀 `,0,`）
     - `user_password=<pass>`
     - `wlan_user_ip`（本机非回环 IPv4）
     - `wlan_user_mac`（对应网卡 MAC，去分隔或 `xx-xx-xx-xx-xx-xx`）
     - `jsVersion=3.3.3`
     - `callback=dr<random>`
   - 成功：JSON `result` 为 `1` 或 `"ok"`
   - suffix 顺序：`@dx` → `@telecom`

### 设备类型（UA）
启动时弹出简单选项（tkinter），用户选择登录请求伪装为：
- **电脑 PC**：`User-Agent` 为桌面 Chrome；Portal 协议账号前缀 `,0,`
- **安卓移动设备**：`User-Agent` 为 Android Chrome；Portal 协议账号前缀 `,1,`

也可用命令行 `--device pc|android` 跳过弹窗；默认弹窗，选完进入监控循环。

### 账号
- 用户名常量：`CAMPUS_USER = "1686345"`
- 密码常量：`CAMPUS_PASS = "CHANGE_ME"`（交付后由用户自行填写）

### 本机信息获取
- IPv4：`socket` 连出 UDP 探测网关，或 `psutil`/系统命令；优先零第三方依赖方案
- MAC：`uuid.getnode()` 或 `ipconfig /all` 解析；以能连上认证为准

### 日志与可观测
- 控制台输出时间戳 + 级别（INFO/WARN/ERROR）
- 记录：探测结果、触发登录、协议/suffix 尝试、成功/失败

### 打包
- `PyInstaller --onefile --console`
- 不把密码写入额外可提交文件；脚本内常量即可

## [S3] Out of Scope
- 不实现注销 / 解绑 MAC / 修改密码
- 不做 GUI 托盘图标
- 不自动开机自启安装器（可由用户自行配任务计划）
- 不破解验证码（本门户当前登录页无强制验证码）
- 不修改学校认证服务器任何数据

## Tasks
- [ ] T1: 实现 `src/campus_auto_login.py` 探测与主循环 — acceptance: 脚本可独立运行，每 10s 探测并打印状态；异常时进入登录流程 (covers: S2)
- [ ] T2: 实现 ACSetting / Portal 双协议登录与后缀回退 — acceptance: 函数按序尝试 @dx/@telecom；不调用 Logout；成功判定符合 S2 (covers: S2)
- [ ] T3: PyInstaller 打包 exe — acceptance: `dist/campus_auto_login.exe` 可双击或命令行启动 (covers: S2)
- [ ] T4: 启动时设备类型选择（PC/安卓）— acceptance: 无 `--device` 时弹出选项；`--device pc|android` 可无交互；UA 与 Portal 前缀随选择切换 (covers: S2)
- [ ] T5: 静态验证与 dry-run — acceptance: 语法检查通过；在**不断网**条件下 dry-run 探测通过；登录函数可被调用但默认 dry-run 不发真实认证 (covers: S2; depends: T1, T2, T4)
- [ ] T6: PyInstaller 打包 exe — acceptance: `dist/campus_auto_login.exe` 存在且 `--help` 可用 (covers: S2; depends: T1, T2, T4)
