#!/usr/bin/env python3
"""校园网 Dr.COM 自动登录监控（Windows）。

首次运行会在程序同目录生成 config.json，填写账号/密码/UA/登录地址后重启即可。
每 10s 探测公网；失败则自动登录。绝不调用注销接口。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

DRY_RUN = False

UA_PC = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
UA_ANDROID = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36"
)

SUCCESS_MARK = "Dr.COMWebLoginID_3.htm"
FAIL_MARK = "Dr.COMWebLoginID_2.htm"
JS_VERSION = "3.3.3"


@dataclass(frozen=True)
class DeviceProfile:
    key: str
    label: str
    ua: str
    account_prefix: str


DEVICES = {
    "pc": DeviceProfile("pc", "电脑 PC", UA_PC, ",0,"),
    "android": DeviceProfile("android", "安卓移动设备", UA_ANDROID, ",1,"),
}


@dataclass
class AppSettings:
    username: str = "1686345"
    password: str = "CHANGE_ME"
    device_mode: str = "pc"  # pc | android | ""(每次启动弹窗)
    portal_host: str = "192.168.200.2"
    portal_port: int = 801
    check_interval_sec: int = 10
    fail_threshold: int = 2
    login_cooldown_sec: int = 30
    probe_url: str = "http://www.msftconnecttest.com/connecttest.txt"
    probe_ok_token: str = "Microsoft Connect Test"
    account_suffixes: list[str] = field(default_factory=lambda: ["@dx", "@telecom"])
    carrier_label: str = "校园电信"

    @property
    def eportal_base(self) -> str:
        return f"http://{self.portal_host}:{self.portal_port}/eportal/"

    @property
    def acsetting_login(self) -> str:
        return f"{self.eportal_base}?c=ACSetting&a=Login"

    @property
    def portal_login(self) -> str:
        return f"{self.eportal_base}?c=Portal&a=login"


def app_dir() -> Path:
    """exe 旁目录；源码运行时为脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def config_path() -> Path:
    return app_dir() / "config.json"


def default_config_doc() -> dict[str, Any]:
    s = AppSettings()
    return {
        "_说明1": "首次运行自动生成。请用记事本修改后保存，再重新启动程序。",
        "_说明2": "password 必填校园网密码；username 为学号/工号（不要带 @dx）。",
        "_说明3": "device_mode: pc=电脑UA / android=安卓UA / 留空字符串则每次启动弹窗选择。",
        "_说明4": "portal_host 一般为 192.168.200.2；portal_port 一般为 801。",
        "_说明5": "account_suffixes 为运营商后缀，校园电信常用 @dx，备选 @telecom。",
        "username": s.username,
        "password": s.password,
        "device_mode": s.device_mode,
        "portal_host": s.portal_host,
        "portal_port": s.portal_port,
        "check_interval_sec": s.check_interval_sec,
        "fail_threshold": s.fail_threshold,
        "login_cooldown_sec": s.login_cooldown_sec,
        "probe_url": s.probe_url,
        "probe_ok_token": s.probe_ok_token,
        "account_suffixes": list(s.account_suffixes),
        "carrier_label": s.carrier_label,
    }


def write_default_config(path: Path) -> None:
    path.write_text(
        json.dumps(default_config_doc(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_settings(path: Path) -> AppSettings:
    if not path.exists():
        write_default_config(path)
        return AppSettings()

    raw = json.loads(path.read_text(encoding="utf-8"))
    base = asdict(AppSettings())
    for key in base:
        if key in raw and raw[key] is not None:
            base[key] = raw[key]
    # 清理后缀列表
    suffixes = base.get("account_suffixes") or ["@dx", "@telecom"]
    if isinstance(suffixes, str):
        suffixes = [suffixes]
    base["account_suffixes"] = [str(x) for x in suffixes]
    device = str(base.get("device_mode") or "").strip().lower()
    if device not in ("", "pc", "android"):
        device = "pc"
    base["device_mode"] = device
    base["portal_port"] = int(base["portal_port"])
    base["check_interval_sec"] = int(base["check_interval_sec"])
    base["fail_threshold"] = int(base["fail_threshold"])
    base["login_cooldown_sec"] = int(base["login_cooldown_sec"])
    return AppSettings(**base)


def log(level: str, msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def _join_query(url: str, params: dict) -> str:
    sep = "&" if ("?" in url) else "?"
    return url + sep + urllib.parse.urlencode(params)


def _redact_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 2:
        return "**"
    return value[0] + "***" + value[-1]


def _decode_body(raw: bytes) -> str:
    for enc in ("utf-8", "gb2312", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 设备选择 UI
# ---------------------------------------------------------------------------
def pick_device_interactive() -> str:
    try:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.title("校园网自动登录 - 设备类型")
        root.resizable(False, False)
        result = {"device": None}

        frm = ttk.Frame(root, padding=16)
        frm.grid()
        ttk.Label(frm, text="请选择登录请求的设备类型（User-Agent）").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        var = tk.StringVar(value="pc")
        ttk.Radiobutton(
            frm, text="电脑 PC（前缀 ,0,）", variable=var, value="pc"
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Radiobutton(
            frm, text="安卓移动设备（前缀 ,1,）", variable=var, value="android"
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=2)

        def on_ok() -> None:
            result["device"] = var.get()
            root.destroy()

        ttk.Button(frm, text="开始监控", command=on_ok).grid(
            row=3, column=0, columnspan=2, pady=(12, 0)
        )
        root.protocol("WM_DELETE_WINDOW", on_ok)
        root.mainloop()
        return result["device"] or "pc"
    except Exception as exc:  # noqa: BLE001
        log("WARN", f"无法弹出图形界面，改用命令行选择: {exc}")
        print("请选择设备类型: 1=电脑 PC  2=安卓移动设备 [默认1]")
        try:
            choice = input("> ").strip()
        except EOFError:
            choice = ""
        return "android" if choice.startswith("2") else "pc"


# ---------------------------------------------------------------------------
# 网络 / 本机信息
# ---------------------------------------------------------------------------
def probe_network(settings: AppSettings, timeout: float = 5.0) -> bool:
    req = urllib.request.Request(
        settings.probe_url,
        headers={
            "User-Agent": UA_PC,
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(256).decode("utf-8", errors="ignore")
            return resp.status == 200 and settings.probe_ok_token in body
    except Exception:
        return False


def _is_usable_ipv4(ip: str) -> bool:
    if not ip or ip.startswith("127.") or ip == "0.0.0.0":
        return False
    if ip.startswith("198.18.") or ip.startswith("198.19."):
        return False
    return True


def get_local_ipv4(portal_host: str) -> str:
    for target in ((portal_host, 80), ("223.5.5.5", 53), ("8.8.8.8", 53)):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(2)
            s.connect(target)
            ip = s.getsockname()[0]
            if _is_usable_ipv4(ip):
                return ip
        except Exception:
            pass
        finally:
            s.close()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if _is_usable_ipv4(ip):
                return ip
    except Exception:
        pass
    return "0.0.0.0"


def get_mac() -> str:
    try:
        import subprocess

        out = subprocess.check_output(
            ["ipconfig", "/all"],
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).decode("gbk", errors="ignore")
        m = re.search(r"Physical Address[.\s]*:\s*([0-9A-Fa-f\-]{17})", out)
        if m:
            return m.group(1).upper()
        m = re.search(r"物理地址[.\s]*:\s*([0-9A-Fa-f\-]{17})", out)
        if m:
            return m.group(1).upper()
    except Exception:
        pass
    node = uuid.getnode()
    parts = [f"{(node >> ele) & 0xFF:02X}" for ele in range(40, -1, -8)]
    return "-".join(parts)


def http_request(
    url: str,
    method: str = "GET",
    data: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 8.0,
) -> tuple[int, str]:
    hdrs = {
        "User-Agent": UA_PC,
        "Accept": "*/*",
        "Connection": "close",
    }
    if headers:
        hdrs.update(headers)

    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")

    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _decode_body(resp.read())
    except urllib.error.HTTPError as e:
        try:
            text = _decode_body(e.read())
        except Exception:
            text = str(e)
        return e.code, text
    except Exception as e:
        return 0, str(e)


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------
def login_acsetting(settings: AppSettings, device: DeviceProfile) -> bool:
    for suffix in settings.account_suffixes:
        account = f"{settings.username}{suffix}"
        payload = {
            "DDDDD": account,
            "upass": settings.password,
            "0MKKey": "123456",
        }
        log("INFO", f"[ACSetting] 尝试账号 {account}")
        if DRY_RUN:
            safe = {
                "DDDDD": account,
                "upass": _redact_secret(settings.password),
                "0MKKey": "***",
            }
            log("INFO", f"[ACSetting][DRY-RUN] 跳过 POST payload={safe}")
            continue

        status, text = http_request(
            settings.acsetting_login,
            method="POST",
            data=payload,
            headers={"User-Agent": device.ua},
            timeout=8.0,
        )
        log("INFO", f"[ACSetting] HTTP {status} len={len(text)}")
        if SUCCESS_MARK in text:
            log("INFO", f"[ACSetting] 成功标记命中: {account}")
            return True
        if FAIL_MARK in text:
            log("WARN", f"[ACSetting] 失败标记: {account}")
            continue
        log("INFO", f"[ACSetting] 无明确标记，视为可能成功: {account}")
        return True
    return False


def login_portal(settings: AppSettings, device: DeviceProfile) -> bool:
    ip = get_local_ipv4(settings.portal_host)
    mac = get_mac()
    log("INFO", f"[Portal] 本机 IP={ip} MAC={mac} device={device.key}")

    for suffix in settings.account_suffixes:
        account = f"{device.account_prefix}{settings.username}{suffix}"
        callback = f"dr{random.randint(1000, 99999)}"
        params = {
            "login_method": "1",
            "user_account": account,
            "user_password": settings.password,
            "wlan_user_ip": ip,
            "wlan_user_ipv6": "",
            "wlan_user_mac": mac,
            "wlan_ac_ip": "",
            "wlan_ac_name": "",
            "jsVersion": JS_VERSION,
            "callback": callback,
            "v": str(random.randint(500, 10000)),
        }
        log("INFO", f"[Portal] 尝试账号 {account}")
        if DRY_RUN:
            safe_params = dict(params)
            safe_params["user_password"] = _redact_secret(settings.password)
            log(
                "INFO",
                f"[Portal][DRY-RUN] 跳过 GET "
                f"{_join_query(settings.portal_login, safe_params)[:140]}...",
            )
            continue

        status, text = http_request(
            _join_query(settings.portal_login, params),
            method="GET",
            headers={"User-Agent": device.ua},
            timeout=8.0,
        )
        log("INFO", f"[Portal] HTTP {status} body={text[:200]!r}")

        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            log("WARN", "[Portal] 响应中无 JSON 对象")
            continue
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            log("WARN", "[Portal] JSON 解析失败")
            continue

        result = str(data.get("result", "")).strip().lower()
        if result in {"1", "ok"}:
            log("INFO", f"[Portal] 成功: {account} msg={data.get('msg', '')}")
            return True
        log("WARN", f"[Portal] 失败 result={result} msg={data.get('msg', '')}")
    return False


def attempt_login(settings: AppSettings, device: DeviceProfile) -> bool:
    if not settings.password or settings.password == "CHANGE_ME":
        log(
            "ERROR",
            f"密码未配置：请编辑 {config_path()} 中的 password 字段",
        )
        return False

    log(
        "INFO",
        f"开始登录 carrier={settings.carrier_label} device={device.label} "
        f"user={settings.username} portal={settings.portal_host}",
    )

    ok = login_acsetting(settings, device)
    if ok and not DRY_RUN:
        time.sleep(2)
        if probe_network(settings):
            log("INFO", "ACSetting 登录后探测成功")
            return True
        log("WARN", "ACSetting 报成功但探测仍失败，尝试 Portal")

    ok = login_portal(settings, device)
    if ok and not DRY_RUN:
        time.sleep(2)
        if probe_network(settings):
            log("INFO", "Portal 登录后探测成功")
            return True
        log("WARN", "Portal 报成功但探测仍失败")

    if DRY_RUN:
        log("INFO", "DRY-RUN 结束（未发送真实认证）")
        return False
    return False


def main_loop(settings: AppSettings, device: DeviceProfile) -> None:
    fail_streak = 0
    last_login_ts = 0.0

    log(
        "INFO",
        f"监控启动 interval={settings.check_interval_sec}s device={device.label}",
    )
    log("INFO", f"探测 URL: {settings.probe_url}")
    log("INFO", "安全约束: 不会调用注销接口")

    while True:
        online = probe_network(settings)
        if online:
            if fail_streak:
                log("INFO", "网络已恢复")
            fail_streak = 0
            log("INFO", "网络正常")
        else:
            fail_streak += 1
            log(
                "WARN",
                f"网络异常 fail_streak={fail_streak}/{settings.fail_threshold}",
            )
            now = time.time()
            cooling = now - last_login_ts < settings.login_cooldown_sec
            if fail_streak >= settings.fail_threshold and not cooling:
                success = attempt_login(settings, device)
                last_login_ts = time.time()
                if success:
                    fail_streak = 0
                else:
                    log("ERROR", "本轮登录未成功，等待冷却后重试")
            elif cooling:
                log("INFO", "登录冷却中，跳过")

        try:
            time.sleep(settings.check_interval_sec)
        except KeyboardInterrupt:
            log("INFO", "收到中断，退出")
            return


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="校园网 Dr.COM 自动登录监控")
    p.add_argument(
        "--config",
        help="配置文件路径；默认程序同目录 config.json",
    )
    p.add_argument(
        "--device",
        choices=("pc", "android"),
        help="覆盖配置中的 device_mode",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只模拟登录请求，不发送真实认证（调试用）",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="只探测一次并退出",
    )
    p.add_argument(
        "--login-once",
        action="store_true",
        help="立即尝试一次登录后退出",
    )
    p.add_argument(
        "--init-config",
        action="store_true",
        help="仅生成默认 config.json（已存在则不覆盖）后退出",
    )
    return p.parse_args(argv)


def resolve_device(settings: AppSettings, cli_device: Optional[str]) -> DeviceProfile:
    if cli_device:
        log("INFO", f"命令行覆盖设备类型: {cli_device}")
        return DEVICES[cli_device]
    mode = (settings.device_mode or "").strip().lower()
    if mode in DEVICES:
        log("INFO", f"使用配置文件设备类型: {DEVICES[mode].label}")
        return DEVICES[mode]
    key = pick_device_interactive()
    log("INFO", f"用户选择设备类型: {DEVICES[key].label}")
    return DEVICES[key]


def main(argv: Optional[list[str]] = None) -> int:
    global DRY_RUN
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    args = parse_args(argv)
    DRY_RUN = bool(args.dry_run)

    cfg = Path(args.config).expanduser().resolve() if args.config else config_path()
    if args.init_config:
        if cfg.exists():
            log("INFO", f"配置已存在，不覆盖: {cfg}")
        else:
            write_default_config(cfg)
            log("INFO", f"已生成配置: {cfg}")
        return 0

    created = not cfg.exists()
    settings = load_settings(cfg)
    if created:
        log("INFO", f"首次运行，已生成配置文件: {cfg}")
        log("INFO", "请用记事本打开并填写 password 等字段，然后重新启动程序。")
    else:
        log("INFO", f"加载配置: {cfg}")

    device = resolve_device(settings, args.device)

    if args.once:
        ok = probe_network(settings)
        log("INFO", f"一次性探测结果: {'正常' if ok else '异常'}")
        return 0 if ok else 1

    if args.login_once:
        ok = attempt_login(settings, device)
        return 0 if ok else 1

    try:
        main_loop(settings, device)
    except KeyboardInterrupt:
        log("INFO", "退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
