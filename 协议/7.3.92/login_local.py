# -*- coding: utf-8 -*-
"""Prompt locally for credentials, then show account information."""
import getpass
import os
from pathlib import Path
import runpy
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW("运动世界校园 - 本地登录")
    root = Path(__file__).resolve().parent
    os.chdir(root)
    if not os.environ.get("CHROMIUM_PATH"):
        for variable, relative in (
            ("ProgramFiles", "Google/Chrome/Application/chrome.exe"),
            ("ProgramFiles(x86)", "Microsoft/Edge/Application/msedge.exe"),
        ):
            base = os.environ.get(variable)
            if base and (Path(base) / relative).is_file():
                os.environ["CHROMIUM_PATH"] = str(Path(base) / relative)
                break

    print("运动世界校园：本地登录")
    print("成功登录后在本机保存会话，后续自动复用；不保存密码。")
    print("登录后查询档案、规则和历史记录。\n")

    from sport_client import SportClient792

    client = SportClient792()

    def progress(message):
        if message.startswith(("[1/3]", "[2/3]", "[2.1/3]", "[3/3]")):
            print(message, flush=True)

    if client.token and client.uid:
        print("使用本地已保存的会话。")
    else:
        username = input("手机号：").strip()
        password = getpass.getpass("密码（输入时不显示）：")
        if not username or not password:
            print("手机号或密码为空，已退出。")
            return
        try:
            result = client.login(username, password, progress_callback=progress)
        finally:
            password = None
        if not result.success:
            print(f"登录失败，错误码：{result.error_code}，原因：{result.message}")
            return

    print("\n登录成功，正在查询账号信息……\n")
    previous_argv = sys.argv
    try:
        sys.argv = [str(root / "cli.py"), "-t", client.token, "--uid", str(client.uid),
                    "--unid", str(client.unid),
                    "--profile", "--rules", "--history"]
        runpy.run_path(str(root / "cli.py"), run_name="__main__")
    finally:
        sys.argv = previous_argv


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n已取消。")
    except Exception as exc:
        print(f"\n操作失败：{type(exc).__name__}。请保留此窗口以便排查。")
    finally:
        try:
            input("\n按回车关闭窗口……")
        except (KeyboardInterrupt, EOFError):
            pass
