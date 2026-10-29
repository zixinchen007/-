# -*- coding: utf-8 -*-
import argparse
import json
import os
from pathlib import Path
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

cur_dir = os.path.dirname(os.path.abspath(__file__))
if cur_dir not in sys.path:
    sys.path.insert(0, cur_dir)

from sport_client import SportClient792
from history.models import parse_history_records, history_record_status
from running.motion import MotionOptions
from running.motion_v3 import MotionV3Options
from core.local_state import load_session


def main():
    parser = argparse.ArgumentParser(description="运动世界校园 7.3.92 纯协议套件")
    parser.add_argument("-u", "--username", help="登录账号 (手机号)")
    parser.add_argument("-p", "--password", help="登录密码")
    parser.add_argument("-t", "--token", help="已有登录 Token")
    parser.add_argument("--uid", help="已有用户 UID")
    parser.add_argument("--unid", help="已有学校 UNID")
    parser.add_argument("--sync-time", action="store_true", help="执行服务端时间对齐")
    parser.add_argument("--rules", action="store_true", help="查询跑步规则与电子围栏")
    parser.add_argument("--checkpoints", action="store_true", help="单独查询官方计分跑点位，不上传成绩")
    parser.add_argument("--profile", action="store_true", help="查询学生个人档案")
    parser.add_argument("--history", action="store_true", help="查询最近历史记录与打卡日历")
    parser.add_argument("--logout", action="store_true", help="退出已有会话，确认失效后清理本地缓存")
    parser.add_argument("--logout-after-run", action="store_true", help="本次 --run 结束或异常时尝试退出会话")

    # 跑步相关选项
    parser.add_argument("--run", action="store_true", help="触发一键全时序跑步与结算")
    parser.add_argument("--mode", type=int, default=1, choices=[1, 6], help="运动类型: 1计分跑 6自由跑")
    parser.add_argument("--distance", type=int, default=2150, help="跑步目标距离 (米，默认 2150)")
    parser.add_argument("--time-sec", type=int, default=760, help="跑步耗时 (秒，默认 760)")
    parser.add_argument("--route-file", help="显式闭合跑道 JSON（coord_type=bd09，loop 为纬度/经度点列）")
    parser.add_argument("--motion-model", choices=["jog", "legacy", "v3"], default="jog",
                        help="运动模型: jog 连续慢跑（默认），legacy 原匀速，v3 实验运动约束模型")
    parser.add_argument("--seed", type=int, help="连续慢跑随机种子，用于复现同一组运动变化")

    # 广告相关选项
    parser.add_argument("--watch-ad", action="store_true", help="模拟看广告与激励视频曝光链路")
    parser.add_argument("--ad-pos", default="4", choices=["1", "2", "4"], help="广告位: 1Banner 2弹窗 4激励视频(默认)")
    parser.add_argument("--ad-duration", type=int, default=15, help="广告观看耗时 (秒，默认 15)")

    args = parser.parse_args()
    if args.logout and any((args.username, args.password, args.sync_time, args.rules,
                           args.checkpoints, args.profile, args.history, args.run,
                           args.watch_ad, args.logout_after_run, args.route_file, args.seed is not None)):
        parser.error('--logout 不能与登录、查询或运行操作组合')
    if args.logout and (bool(args.token) != bool(args.uid) or (args.unid and not args.token)):
        parser.error('退出显式会话时必须同时提供 --token 和 --uid')
    if args.logout_after_run and not args.run:
        parser.error('--logout-after-run 需要与 --run 一起使用')
    if args.seed is not None and (not args.run or args.motion_model == "legacy"):
        parser.error("--seed 仅支持连续慢跑模型的 --run")

    route_loop = None
    if args.route_file:
        if not args.run:
            parser.error("--route-file 需要与 --run 一起使用")
        try:
            route = json.loads(Path(args.route_file).read_text(encoding="utf-8-sig"))
            if not isinstance(route, dict) or route.get("coord_type") != "bd09" or not isinstance(route.get("loop"), list):
                raise ValueError("需要 coord_type=bd09 和 loop 点列；不能混用高德 GCJ-02 坐标")
            # Validate the file before any authentication or network operation.
            from running.routes import checkpoint_loop
            route_loop = checkpoint_loop(route["loop"], [], args.distance)
        except (OSError, TypeError, ValueError) as exc:
            parser.error(f"跑道文件无效: {exc}")

    if args.logout and not args.token and load_session() is None:
        print('退出完成：本机没有已保存的会话。')
        return 0
    client = SportClient792(uid=args.uid, token=args.token, unid=args.unid)
    if args.logout:
        return _logout(client)
    code = 0
    try:
        code = _execute_actions(args, client, route_loop)
    finally:
        if args.logout_after_run and _logout(client) != 0:
            code = 2
    return code


def _logout(client):
    try:
        confirmed = client.logout()
    except Exception:
        confirmed = False
    if confirmed:
        print('退出完成：会话已失效或没有活动会话；设备标识保留。')
        return 0
    print('退出未确认：保留会话以便重试退出；如成绩已提交，请勿重复提交。')
    return 2


def _execute_actions(args, client, route_loop):

    # 1. 登录认证
    if args.username and args.password:
        res = client.login(args.username, args.password, progress_callback=print)
        if not res.success:
            print(f"登录失败: code={res.error_code} 原因={res.message}")
            return 2
        print('登录成功：会话已保存到本机。')

    # 2. 时钟对齐
    if args.sync_time or (not args.rules and not args.checkpoints and not args.profile and not args.history and not args.run and not args.watch_ad):
        st = client.sync_server_time()
        print(f"时钟同步: 服务端时间={st.server_datetime} 偏斜={st.offset_seconds:+.3f}s")

    # 3. 个人档案
    if args.profile:
        if not client.token:
            print("个人档案: 失败 原因=缺少Token")
        else:
            prof = client.get_user_profile()
            if prof:
                print(f"个人档案: 姓名={prof.name} 性别={prof.sex} 学校={prof.campus_name} 学院={prof.depart} 班级={prof.grade_class} 学号={prof.student_id}")
            else:
                print("个人档案: 失败 原因=查询为空")

    # 4. 跑步规则与围栏
    if args.rules:
        if not client.token or not client.uid:
            print("跑步规则: 失败 原因=缺少Token或UID")
        else:
            rule = client.get_run_policy(run_mode=args.mode)
            fences = client.get_geo_fences()
            mode_name = "计分跑" if args.mode == 1 else "自由跑"
            if rule:
                print(f"跑步规则: 模式={mode_name} 单日上限={rule.day_goal}m 单次最低={rule.min_distance}m 配速={rule.speed_top}~{rule.speed_bottom}分/km 步频={rule.step_top}~{rule.step_bottom}步/分 围栏数={len(fences)}")
            else:
                print(f"跑步规则: 失败 模式={mode_name} 原因=规则查询为空")

    if args.checkpoints:
        if not client.token:
            print("官方点位: 失败 原因=缺少Token")
        else:
            fences = client.get_geo_fences()
            usable = [fence for fence in fences if fence.polygon_points]
            if not usable:
                print("官方点位: 失败 原因=未取得有效围栏锚点")
            else:
                points = client.policy.get_checkpoints_for_run(usable[0])
                if points:
                    print(f"官方点位: 成功 来源=pointsResModels 点数={len(points)}；未上传成绩")
                else:
                    print(f"官方点位: 失败 原因={client.policy.last_checkpoint_error}")

    # 5. 广告模拟
    if args.watch_ad:
        ad_res = client.watch_ad(
            ad_position=args.ad_pos,
            duration_sec=args.ad_duration,
            progress_callback=print
        )
        if ad_res.success:
            print(f"广告模拟: 成功 位置={args.ad_pos} 事件数={ad_res.events_count} 耗时={args.ad_duration}s")
        else:
            print(f"广告模拟: 失败 位置={args.ad_pos} 原因={ad_res.message}")

    # 6. 历史记录
    if args.history:
        if not client.token or not client.uid:
            print("历史记录: 失败 原因=缺少Token或UID")
        else:
            h_res = client.get_history_records(page_num=1, page_size=5)
            if isinstance(h_res, dict) and h_res.get("error") == 10000:
                try:
                    recs = parse_history_records(h_res.get("data"))
                except (TypeError, ValueError) as exc:
                    print(f"历史记录: 失败 原因=响应解析失败: {exc}")
                else:
                    print(f"历史记录: 条数={len(recs)}")
                    for r in recs:
                        status_str = history_record_status(r)
                        print(f"记录明细: rrid={r.get('rrid')} 里程={r.get('totalDis')}m 耗时={r.get('totalTime')}s 状态={status_str}")
            else:
                err_msg = h_res.get("message") if isinstance(h_res, dict) else "网络异常"
                print(f"历史记录: 失败 原因={err_msg}")

    # 7. 拟人跑步提交
    if args.run:
        if not client.token or not client.uid:
            print("跑步提交: 失败 原因=缺少Token或UID")
            return 2
        else:
            mode_name = "计分跑" if args.mode == 1 else "自由跑"
            run_res = client.execute_run(
                target_distance_m=args.distance,
                target_time_sec=args.time_sec,
                sport_type=args.mode,
                progress_callback=print,
                route_loop=route_loop,
                motion_options=(MotionV3Options(seed=args.seed) if args.motion_model == "v3" else
                                MotionOptions(seed=args.seed) if args.motion_model == "jog" else None)
            )
            if run_res.success:
                valid_str = "即时回查通过，待App复核" if run_res.is_valid is True else "即时回查未达标" if run_res.is_valid is False else "待核验"
                print(f"跑步提交: 成功 模式={mode_name} rrid={run_res.rrid} 里程={run_res.total_distance}m 耗时={run_res.total_time}s 状态={valid_str}")
                print(run_res.message)
            else:
                print(f"跑步提交: 失败 模式={mode_name} code={run_res.error_code} 原因={run_res.message}")
                return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
