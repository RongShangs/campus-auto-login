#!/usr/bin/env python3
"""校园网 Dr.COM 自动登录监控（Windows）。

每 10s 探测公网连通性；失败则按「校园电信」自动登录。
绝不调用注销接口。
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
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# 配置（交付后请改密码）
# ---------------------------------------------------------------------------
CAMPUS_USER = "1686345"
CAMPUS_PASS = "CHANGE_ME"  # TODO: 填入校园网密码

CHECK_INTERVAL_SEC = 10
FAIL_THRESHOLD = 2
LOGIN_COOLDOWN_SEC = 30
PROBE_URL = "http://www.msftconnecttest.com/connecttest.txt"
PROBE_OK_TOKEN = "Microsoft Connect Test"

PORTAL_HOST = "192.168.200.2"
EPORTAL_BASE = f"http://{PORTAL_HOST}:801/eportal/"
ACSETTING_LOGIN = f"{EPORTAL_BASE}?c=ACSetting&a=Login"
PORTAL_LOGIN = f"{EPORTAL_BASE}?c=Portal&a=login"

SUCCESS_MARK = "Dr.COMWebLoginID_3.htm"
FAIL_MARK = "Dr.COMWebLoginID_2.htm"
JS_VERSION = "3.3.3"

# 运营商后缀：校园电信
SUFFIXES = ("@dx", "@telecom")

UA_PC = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
UA_ANDROID = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36"
)

DRY_RUN = False


@dataclass(frozen=True)
class DeviceProfile:
    key: str
    label: str
    ua: str
    account_prefix: str  # Portal 协议前缀


DEVICES = {
    "pc": DeviceProfile("pc", "电脑 PC", UA_PC, ",0,"),
    "android": DeviceProfile("android", "安卓移动设备", UA_ANDROID, ",1,"),
}


def log(level: str, msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def _join_query(url: str, params: dict) -> str:
    sep = "&" if ("?" in url) else "?"
    return url + sep + urllib.parse.urlencode(params)


# ---------------------------------------------------------------------------
# 设备选择 UI
# ---------------------------------------------------------------------------
def pick_device_interactive() -> str:
    """启动时让用户选择 PC / 安卓。无 GUI 环境时回退控制台输入。"""
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
# 网络探测
# ---------------------------------------------------------------------------
def probe_network(timeout: float = 5.0) -> bool:
    req = urllib.request.Request(
        PROBE_URL,
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
            return resp.status == 200 and PROBE_OK_TOKEN in body
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 本机 IP / MAC
# ---------------------------------------------------------------------------
def _is_usable_ipv4(ip: str) -> bool:
    if not ip or ip.startswith("127.") or ip == "0.0.0.0":
        return False
    # 排除保留/基准测试段（如 198.18.0.0/15）
    if ip.startswith("198.18.") or ip.startswith("198.19."):
        return False
    return True


def get_local_ipv4() -> str:
    # 优先连到认证主机，拿真实校园网出口 IP
    for target in ((PORTAL_HOST, 80), ("223.5.5.5", 53), ("8.8.8.8", 53)):
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
    node = uuid.getnode()
    mac = ":".join(f"{(node >> ele) & 0xFF:02x}" for ele in range(40, -1, -8))
    return mac.upper()


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
            raw = resp.read()
            text = _decode_body(raw)
            return resp.status, text
    except urllib.error.HTTPError as e:
        try:
            text = _decode_body(e.read())
        except Exception:
            text = str(e)
        return e.code, text
    except Exception as e:
        return 0, str(e)


def _decode_body(raw: bytes) -> str:
    for enc in ("utf-8", "gb2312", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 登录：ACSetting POST
# ---------------------------------------------------------------------------
def login_acsetting(user: str, password: str, ua: str) -> bool:
    for suffix in SUFFIXES:
        account = f"{user}{suffix}"
        payload = {
            "DDDDD": account,
            "upass": password,
            "0MKKey": "123456",
        }
        log("INFO", f"[ACSetting] 尝试账号 {account}")
        if DRY_RUN:
            log("INFO", f"[ACSetting][DRY-RUN] 跳过 POST payload={payload}")
            continue

        status, text = http_request(
            ACSETTING_LOGIN,
            method="POST",
            data=payload,
            headers={"User-Agent": ua},
            timeout=8.0,
        )
        log("INFO", f"[ACSetting] HTTP {status} len={len(text)}")
        if SUCCESS_MARK in text:
            log("INFO", f"[ACSetting] 成功标记命中: {account}")
            return True
        if FAIL_MARK in text:
            log("WARN", f"[ACSetting] 失败标记: {account}")
            continue
        log("INFO", "[ACSetting] 无明确标记，继续下一后缀")
    return False


# ---------------------------------------------------------------------------
# 登录：Portal JSONP
# ---------------------------------------------------------------------------
def login_portal(user: str, password: str, device: DeviceProfile) -> bool:
    ip = get_local_ipv4()
    mac = get_mac()
    log("INFO", f"[Portal] 本机 IP={ip} MAC={mac} device={device.key}")

    for suffix in SUFFIXES:
        account = f"{device.account_prefix}{user}{suffix}"
        callback = f"dr{random.randint(1000, 99999)}"
        params = {
            "login_method": "1",
            "user_account": account,
            "user_password": password,
            "wlan_user_ip": ip,
            "wlan_user_ipv6": "",
            "wlan_user_mac": mac,
            "wlan_ac_ip": "",
            "wlan_ac_name": "",
            "jsVersion": JS_VERSION,
            "callback": callback,
            "v": str(random.randint(500, 10000)),
        }
        url = _join_query(PORTAL_LOGIN, params)
        log("INFO", f"[Portal] 尝试账号 {account}")
        if DRY_RUN:
            log("INFO", f"[Portal][DRY-RUN] 跳过 GET url={url[:120]}...")
            continue

        status, text = http_request(
            url,
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


# ---------------------------------------------------------------------------
# 登录编排
# ---------------------------------------------------------------------------
def attempt_login(user: str, password: str, device: DeviceProfile) -> bool:
    if not password or password == "CHANGE_ME":
        log("ERROR", "密码未配置，请修改脚本顶部 CAMPUS_PASS")
        return False

    log("INFO", f"开始登录 device={device.label} user={user}")

    ok = login_acsetting(user, password, device.ua)
    if ok and not DRY_RUN:
        time.sleep(2)
        if probe_network():
            log("INFO", "ACSetting 登录后探测成功")
            return True
        log("WARN", "ACSetting 报成功但探测仍失败，尝试 Portal")

    ok = login_portal(user, password, device)
    if ok and not DRY_RUN:
        time.sleep(2)
        if probe_network():
            log("INFO", "Portal 登录后探测成功")
            return True
        log("WARN", "Portal 报成功但探测仍失败")

    if DRY_RUN:
        log("INFO", "DRY-RUN 结束（未发送真实认证）")
        return False
    return False


def main_loop(user: str, password: str, device: DeviceProfile) -> None:
    fail_streak = 0
    last_login_ts = 0.0

    log("INFO", f"监控启动 interval={CHECK_INTERVAL_SEC}s device={device.label}")
    log("INFO", f"探测 URL: {PROBE_URL}")
    log("INFO", "安全约束: 不会调用注销接口")

    while True:
        online = probe_network()
        if online:
            if fail_streak:
                log("INFO", "网络已恢复")
            fail_streak = 0
            log("INFO", "网络正常")
        else:
            fail_streak += 1
            log("WARN", f"网络异常 fail_streak={fail_streak}/{FAIL_THRESHOLD}")

            now = time.time()
            cooling = now - last_login_ts < LOGIN_COOLDOWN_SEC
            if fail_streak >= FAIL_THRESHOLD and not cooling:
                success = attempt_login(user, password, device)
                last_login_ts = time.time()
                if success:
                    fail_streak = 0
                else:
                    log("ERROR", "本轮登录未成功，等待冷却后重试")
            elif cooling:
                log("INFO", "登录冷却中，跳过")

        try:
            time.sleep(CHECK_INTERVAL_SEC)
        except KeyboardInterrupt:
            log("INFO", "收到中断，退出")
            return


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="校园网 Dr.COM 自动登录监控")
    p.add_argument(
        "--device",
        choices=("pc", "android"),
        help="设备类型；省略则启动时弹出选择",
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
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    global DRY_RUN
    # Windows 控制台中文输出
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = parse_args(argv)
    DRY_RUN = bool(args.dry_run)

    if args.device:
        device = DEVICES[args.device]
        log("INFO", f"使用命令行设备类型: {device.label}")
    else:
        key = pick_device_interactive()
        device = DEVICES[key]
        log("INFO", f"用户选择设备类型: {device.label}")

    if args.once:
        ok = probe_network()
        log("INFO", f"一次性探测结果: {'正常' if ok else '异常'}")
        return 0 if ok else 1

    if args.login_once:
        ok = attempt_login(CAMPUS_USER, CAMPUS_PASS, device)
        return 0 if ok else 1

    try:
        main_loop(CAMPUS_USER, CAMPUS_PASS, device)
    except KeyboardInterrupt:
        log("INFO", "退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
