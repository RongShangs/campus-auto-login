---
feature: campus-auto-login
status: delivered
updated: 2026-09-12
branch: feat/campus-auto-login
commits: 7f0fc10..e27dd7b
---

# 校园网自动登录监控

## Report

**What was built** — 一个 Windows 常驻监控程序：每 10 秒探测 `http://www.msftconnecttest.com/connecttest.txt`，连续失败达到阈值后按「校园电信」自动登录 Dr.COM。登录顺序为 ACSetting POST → Portal JSONP，后缀依次尝试 `@dx`、`@telecom`。启动时可用 tkinter 选择设备类型（电脑 PC / 安卓），也可用 `--device pc|android` 跳过弹窗；Portal 账号前缀分别为 `,0,` / `,1,`。交付源码 `src/campus_auto_login.py` 与单文件 `dist/campus_auto_login.exe`。绝不调用注销接口；dry-run 日志会对密码脱敏。

**Verification** — `python -m py_compile` PASS；`--once` 探测 PASS；`--login-once --dry-run`（pc/android）PASS 且日志无明文密码；PyInstaller 打包 PASS，`dist/campus_auto_login.exe --help` / `--once` PASS。评审 critical（密码进日志、MAC 格式）已修复并重打包。

**Journey log**
1. 实测门户为 Dr.COM 哆点；loginMethod=1（Portal），但保留更简单的 ACSetting 作首选。
2. 当前在线 uid 为 `@telecom`，页面运营商配置为 `@dx`，故双后缀回退。
3. 不对 `192.168.200.2` 发 Logout/真实改密类请求，避免踢下线。
4. dry-run 曾打印完整 payload，评审判 critical 后改为脱敏。
5. 本机 IP 先误取 198.18.x，改为优先连认证主机取校园网地址 10.16.18.21。

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

也可用命令行 `--device pc|android` 跳过弹窗；选择会写回 config.json 的 `device_mode`。
安卓登录时：Portal 优先（账号前缀 `,1,`），ACSetting 附带 `R6=1`（手机终端标记）。

### 配置文件（config.json）
首次运行在 **exe 同目录**（源码运行则为脚本同目录）生成 `config.json`，用记事本填写后重启生效：

| 字段 | 说明 | 默认 |
|---|---|---|
| username | 学号/工号，不带 @dx | 1686345 |
| password | 校园网密码 | CHANGE_ME |
| device_mode | `pc` / `android` / 空字符串则每次启动弹窗 | pc |
| portal_host | 认证主机 | 192.168.200.2 |
| portal_port | 认证端口 | 801 |
| check_interval_sec | 探测间隔 | 10 |
| fail_threshold | 连续失败阈值 | 2 |
| login_cooldown_sec | 登录冷却 | 30 |
| account_suffixes | 运营商后缀列表 | ["@dx","@telecom"] |

`--config` 可指定路径；`--device` 可临时覆盖 device_mode。`config.json` 含密码，不提交 git。

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
- [x] T1: 实现 `src/campus_auto_login.py` 探测与主循环 — acceptance: 脚本可独立运行，每 10s 探测并打印状态；异常时进入登录流程 (covers: S2)
- [x] T2: 实现 ACSetting / Portal 双协议登录与后缀回退 — acceptance: 函数按序尝试 @dx/@telecom；不调用 Logout；成功判定符合 S2 (covers: S2)
- [x] T3: PyInstaller 打包 exe — acceptance: `dist/campus_auto_login.exe` 可双击或命令行启动 (covers: S2)
- [x] T4: 启动时设备类型选择（PC/安卓）— acceptance: 无 `--device` 时弹出选项；`--device pc|android` 可无交互；UA 与 Portal 前缀随选择切换 (covers: S2)
- [x] T5: 静态验证与 dry-run — acceptance: 语法检查通过；在**不断网**条件下 dry-run 探测通过；登录函数可被调用但默认 dry-run 不发真实认证 (covers: S2; depends: T1, T2, T4)
- [x] T6: 评审修复：密码脱敏与 MAC 格式 — acceptance: dry-run 日志无明文密码；MAC 为 xx-xx-xx-xx-xx-xx (covers: S2)
