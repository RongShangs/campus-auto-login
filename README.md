# 重庆邮电大学校园网自动网页登录守护

Windows 下的校园网（Dr.COM / 哆点）自动登录守护进程。  
每 10 秒探测公网连通性；发现断网后自动选择「校园电信」并用预设账号密码重新登录，无需手开网页勾选运营商。

## 功能

- 周期探测公网（默认 `http://www.msftconnecttest.com/connecttest.txt`）
- 连续失败达阈值后自动登录，避免瞬时抖动误触发
- 支持双协议：`ACSetting` POST 与 `Portal` JSONP
- 运营商后缀自动回退：`@dx` → `@telecom`（校园电信）
- 设备类型可选：电脑 PC（前缀 `,0,`）/ 安卓（前缀 `,1,`，并带 `R6=1`）
- 首次运行在 exe 同目录生成 `config.json`，用记事本填写即可
- **不会调用注销接口**，不会主动踢下线当前会话
- dry-run 日志对密码脱敏

## 快速开始

1. 从 [Releases](https://github.com/RongShangs/campus-auto-login/releases) 下载 `campus_auto_login.exe`
2. 放到任意文件夹，双击运行一次 → 旁路生成 `config.json`
3. 用记事本打开 `config.json`，至少修改：
   - `username`：学号
   - `password`：校园网密码
   - `device_mode`：`pc` 或 `android`
4. 再次双击 exe，保持运行即可

## 配置说明（config.json）

| 字段 | 说明 | 默认 |
|------|------|------|
| `username` | 学号/工号（不要带 `@dx`） | `1686345` |
| `password` | 校园网密码 | `CHANGE_ME` |
| `device_mode` | `pc` / `android`；留空则每次启动弹窗选择 | `pc` |
| `portal_host` | 认证主机 | `192.168.200.2` |
| `portal_port` | 认证端口 | `801` |
| `check_interval_sec` | 探测间隔（秒） | `10` |
| `fail_threshold` | 连续失败几次后登录 | `2` |
| `login_cooldown_sec` | 登录冷却（秒） | `30` |
| `account_suffixes` | 运营商后缀列表 | `["@dx","@telecom"]` |

> **注意**：`config.json` 含密码，请勿提交到 Git 或发给他人。

## 命令行参数

```text
campus_auto_login.exe [--config PATH] [--device pc|android] [--dry-run] [--once] [--login-once] [--init-config]
```

| 参数 | 作用 |
|------|------|
| `--config` | 指定配置文件路径（默认 exe 同目录 `config.json`） |
| `--device` | 临时覆盖设备类型，并写回配置 |
| `--dry-run` | 只演练登录流程，不发真实认证 |
| `--once` | 只探测一次网络后退出 |
| `--login-once` | 立即尝试一次登录后退出 |
| `--init-config` | 仅生成默认配置（已存在则不覆盖） |

## 从源码运行

需要 Python 3.10+（Windows）：

```powershell
cd src
python campus_auto_login.py --device pc
```

打包为单文件 exe：

```powershell
pip install pyinstaller
pyinstaller --onefile --console --name campus_auto_login src/campus_auto_login.py
```

## 工作原理（简要）

```text
[每 10s] 探测公网
    │
    ├─ 成功 → 继续监控
    │
    └─ 连续失败 ≥ 阈值
            │
            ├─ 安卓：先 Portal（,1, 前缀）→ 再 ACSetting（R6=1）
            └─ 电脑：先 ACSetting → 再 Portal（,0, 前缀）
                    │
                    └─ 后缀顺序 @dx → @telecom
```

认证系统为 Dr.COM 哆点门户（`192.168.200.2:801/eportal/`）。

## 安全说明

- 密码只保存在本机 `config.json`，仓库与 Release **不含**任何账号密码
- 程序只做「探测 + 登录」，**绝不**调用 Logout / 解绑 MAC / 改密
- 若密码写错或账号异常，日志会报失败并进入冷却，不会无限打爆认证服务器

## 开机自启（可选）

任务计划程序 → 创建任务 → 触发器「登录时」→ 操作指向 `campus_auto_login.exe`。

## License

仅供校园网本人账号自动化使用，请遵守学校网络使用规定。
