# -*- coding: utf-8 -*-
import json
import os
import sys
import time
import urllib.request
import random
import uuid
from dataclasses import replace
from typing import Optional, Dict, Any, Union, List, Callable

cur_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(cur_dir)
for p in [cur_dir, parent_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from core.client import BaseNetSecClient
from profile.client import ProfileClient
from policy.client import PolicyClient
from history.client import HistoryClient
from telemetry.client import TelemetryClient
from .config import (
    URL_OBS_TEMPORARY_URL,
    URL_SAVE_RECORD,
    URL_RUN_DETAIL_CONFIG,
    OBS_BUCKET_NAME
)
from .models import RunningSubmissionResult
from .builder import RunningTrackBuilder
from .obs import bind_record
from .motion import MotionOptions, build_timeline
from .motion_v3 import MotionV3Options, build_timeline as build_v3_timeline
from .route_variation import RouteVariationOptions


class RunningClient(BaseNetSecClient):
    """运动世界校园 7.3.92 跑步提交与 OBS 上传客户端"""

    def __init__(self,
                 uid: Optional[Union[int, str]] = None,
                 token: Optional[str] = None,
                 unid: Optional[Union[int, str]] = None,
                 device_config: Optional[Dict[str, Any]] = None):
        super().__init__(uid=uid, token=token, unid=unid, device_config=device_config)
        self.telemetry = TelemetryClient(device_config=self.device_config)
        self.profile = ProfileClient(uid=uid, token=token, unid=unid, device_config=self.device_config)
        self.policy = PolicyClient(uid=uid, token=token, unid=unid, device_config=self.device_config)
        self.history = HistoryClient(uid=uid, token=token, unid=unid, device_config=self.device_config)

    def set_credentials(self, uid: Optional[Union[int, str]], token: Optional[str], unid: Optional[Union[int, str]] = None):
        super().set_credentials(uid, token, unid)
        self.profile.set_credentials(uid, token, unid)
        self.policy.set_credentials(uid, token, unid)
        self.history.set_credentials(uid, token, unid)

    def request_obs_upload_url(self, object_key: str, method: str = "Put") -> dict:
        """向网关申请华为云 OBS 预签名上传地址 (/api/obs/temporary/url)"""
        body = {
            "bucketName": OBS_BUCKET_NAME,
            "objectKey": object_key,
            "method": method,
            "contentType": "application/json"
        }
        res = self.execute_request("POST", URL_OBS_TEMPORARY_URL, body_plain=body)
        if isinstance(res, dict) and res.get("error") == 10000 and "data" in res:
            data = res["data"]
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    pass
            return {"error": 10000, "data": data}
        return res if isinstance(res, dict) else {"error": -1, "message": str(res)}

    def upload_to_obs(self, signed_url: str, signed_headers: dict, payload: dict) -> dict:
        """直接以 PUT 请求向华为云 OBS 存储桶异步推送轨迹与步频载荷"""
        data_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = dict(signed_headers or {"Content-Type": "application/json"})
        req = urllib.request.Request(signed_url, data=data_bytes, headers=headers, method="PUT")
        try:
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=30) as resp:
                if 200 <= resp.status < 300:
                    return {"error": 10000, "message": "OBS 上传成功"}
                return {"error": resp.status, "message": f"OBS HTTP {resp.status}"}
        except urllib.error.HTTPError as e:
            return {"error": e.code, "message": f"OBS 上传失败: {e.reason}"}
        except Exception as e:
            return {"error": -1, "message": f"OBS 网络异常: {e}"}

    def verify_obs_object(self, object_key: str, payload: dict) -> dict:
        """Read the stored object back; HTTP PUT success alone is insufficient."""
        signed = self.request_obs_upload_url(object_key=object_key, method="Get")
        if signed.get("error") != 10000 or not isinstance(signed.get("data"), dict):
            return {"error": -1, "message": "OBS 回读凭证申请失败"}
        info = signed["data"]
        try:
            request = urllib.request.Request(info["signedUrl"],
                headers=info.get("actualSignedRequestHeaders") or {}, method="GET")
            with urllib.request.urlopen(request, context=self.ssl_ctx, timeout=30) as response:
                data = response.read(8_000_001)
            if len(data) > 8_000_000 or json.loads(data.decode("utf-8")) != payload:
                return {"error": -1, "message": "OBS 回读对象与上传内容不一致"}
            return {"error": 10000}
        except Exception as exc:
            return {"error": -1, "message": f"OBS 回读失败: {type(exc).__name__}"}

    def submit_record(self, save_body: dict) -> dict:
        """核心跑步记录结算上报 (/api/v70260/runnings/save/record)"""
        return self.execute_request("POST", URL_SAVE_RECORD, body_plain=save_body)

    def get_rundetail_config(self, sport_type: int = 1) -> dict:
        """拉取跑步详情页配置 (/api/v70340/activity/rundetail/config)"""
        return self.execute_request("GET", f"{URL_RUN_DETAIL_CONFIG}?sportType={sport_type}")

    def execute_full_run(self,
                         target_distance_m: int = 2100,
                         target_time_sec: int = 780,
                         sport_type: int = 1,
                         progress_callback: Optional[Callable[[str], None]] = None,
                         route_loop: Optional[List[tuple]] = None,
                         motion_options: Optional[MotionOptions | MotionV3Options] = MotionOptions(),
                         route_variation: Optional[RouteVariationOptions] = None) -> RunningSubmissionResult:
        """默认使用连续慢跑；显式传入 motion_options=None 可使用原匀速模型。"""
        def log(msg: str):
            if progress_callback:
                progress_callback(msg)

        if not self.uid or not self.token:
            return RunningSubmissionResult(success=False, message="缺少鉴权凭据 (uid/token)")

        if route_variation is not None:
            try:
                if not isinstance(route_variation, RouteVariationOptions):
                    raise ValueError("走线变化需要 RouteVariationOptions")
                route_variation.validate()
                if route_variation.max_offset_m > 0 and (route_loop is None or motion_options is None):
                    raise ValueError("走线变化需要显式跑道和连续慢跑时间序列")
            except (TypeError, ValueError) as exc:
                return RunningSubmissionResult(success=False, message=f"本地走线配置无效: {exc}")

        # Use the same school policy for point issuance and record construction.
        rule = self.policy.get_run_policy(run_mode=sport_type)
        if rule is None:
            return RunningSubmissionResult(success=False, message="未取得学校规则，停止提交")
        try:
            minimum_distance = rule.min_distance
            minimum_time = int((rule.raw.get("runRuleModel") or {}).get("minRunTime", 0))
            policy = int(rule.raw.get("policy", 1))
            if minimum_distance <= 0 or minimum_time < 0:
                raise ValueError("学校距离或时长规则无效")
        except (TypeError, ValueError, AttributeError) as exc:
            return RunningSubmissionResult(success=False, message=f"学校规则无法解析: {exc}")
        log(f"学校规则: 最低距离={minimum_distance}m 最低时长={minimum_time}s")

        # 1. 冷启动时间校准与行为埋点
        st1 = self.profile.get_server_time()
        t_now_ms = st1.server_timestamp_ms
        log(f"时钟校准: 成功 时间戳={t_now_ms} 偏斜={st1.offset_seconds:+.3f}s")
        if self.unid:
            self.telemetry.send_startup_pv(uid=int(self.uid), unid=int(self.unid))
            self.telemetry.send_page_exposure(unid=int(self.unid))

        # 2. 拉取电子围栏与打卡点
        fences = self.policy.get_geo_fence()
        run_uuid = str(uuid.uuid4())
        checkpoints = []
        center_lat, center_lng = 0.0, 0.0  # 默认通用占位坐标
        if fences and len(fences) > 0:
            f = fences[0]
            checkpoints = [{"lat": cp.lat, "lng": cp.lng, "name": cp.name} for cp in f.check_points]
            if f.polygon_points:
                center_lat = f.polygon_points[0].lat
                center_lng = f.polygon_points[0].lng
            if sport_type == 1 and not checkpoints:
                live = self.policy.get_checkpoints_for_run(f, uuid_str=run_uuid, rule=rule)
                if live:
                    checkpoints = [point.as_checkpoint() for point in live]
                    log(f"官方点位: 成功 来源=pointsResModels 点数={len(checkpoints)}")
                else:
                    log(f"官方点位: 失败 原因={self.policy.last_checkpoint_error}；继续使用本地圆形五点兜底")
            log(f"围栏关联: 成功 名称={f.fence_name} 点数={len(checkpoints)}")
        else:
            log("围栏关联: 未命中 策略=环形合规兜底")

        # 3. 拟人运动学轨迹生成
        stop_time_ms = t_now_ms
        start_time_ms = stop_time_ms - (target_time_sec * 1000)
        total_steps = int((target_distance_m / 0.72) * random.uniform(0.96, 1.04)) if motion_options is None else 0

        builder = RunningTrackBuilder(uid=int(self.uid), unid=int(self.unid or 0),
                                      sport_type=sport_type, uuid_str=run_uuid)
        try:
            prepared = None
            if isinstance(motion_options, MotionV3Options):
                motion_options.validate()
                if motion_options.seed is None:
                    motion_options = replace(motion_options, seed=random.SystemRandom().randrange(2**64))
                prepared = builder.prepare_route(center_lat, center_lng, checkpoints,
                    target_distance_m, route_loop, route_variation, motion_options.seed)
                motion_route = prepared.motion_route
                timeline = build_v3_timeline(motion_route, target_time_sec, motion_options)
                motion_route.validate_bends(timeline, motion_options.lateral_accel_m_s2)
            else:
                timeline = build_timeline(target_distance_m, target_time_sec, motion_options) if motion_options is not None else None
            if timeline is not None:
                total_steps = timeline.total_steps
                model_name = 'motion-v3-python-1' if isinstance(motion_options, MotionV3Options) else '连续慢跑'
                log(f"运动模型: {model_name} seed={timeline.seed}")
            else:
                log("运动模型: 原匀速模型")
            gps_pts, five_pts = builder.generate_track_around_points(
                center_lat=center_lat,
                center_lng=center_lng,
                checkpoints=checkpoints,
                total_distance_m=target_distance_m,
                total_time_sec=target_time_sec,
                start_time_ms=start_time_ms,
                route_loop=route_loop,
                timeline=timeline,
                route_variation=route_variation,
                prepared_route=prepared
            )

            if builder.route_geometry is not None:
                log(f"走线模型: 多圈微变 最大偏移={route_variation.max_offset_m}m seed={builder.route_geometry.seed}")

            save_record, obs_payload, _ = builder.build_submission_payload(
                total_distance_m=target_distance_m,
                total_time_sec=target_time_sec,
                total_steps=total_steps,
                start_time_ms=start_time_ms,
                stop_time_ms=stop_time_ms,
                gps_points=gps_pts,
                five_points=five_pts,
                sel_distance_m=minimum_distance,
                sel_run_time_s=minimum_time,
                policy=policy,
                run_area_id=self.policy.last_checkpoint_area_id,
                geo_fences_json=json.dumps([f.raw for f in fences], separators=(",", ":")),
                timeline=timeline
            )
        except (TypeError, ValueError) as exc:
            return RunningSubmissionResult(success=False, message=f"本地一致性校验失败: {exc}")
        log(f"轨迹合成: 成功 距离={target_distance_m}m 耗时={target_time_sec}s 点数={len(gps_pts)} 步数={total_steps} 配速={save_record["speed"]}")

        # 4. Settle once to obtain the record ID before associating OBS data.
        sub_res = self.submit_record(save_record)
        if not isinstance(sub_res, dict) or sub_res.get("error") != 10000:
            code = sub_res.get("error", -1) if isinstance(sub_res, dict) else -1
            msg = sub_res.get("message", "结算上报失败") if isinstance(sub_res, dict) else str(sub_res)
            log(f"成绩结算: 失败 code={code} 原因={msg}")
            return RunningSubmissionResult(success=False, error_code=code, message=msg, raw=sub_res)

        data = sub_res.get("data") or {}
        data = data if isinstance(data, dict) else {}
        value = data.get("rrid") or data.get("id")
        rrid = int(value) if not isinstance(value, bool) and str(value).isdigit() and int(value) > 0 else None
        log(f"成绩结算: 成功 rrid={rrid}")

        # 5. Upload both map lookup paths, with the accepted record ID in the object.
        uploaded = verified = 0
        if rrid:
            try:
                bound_payload, keys = bind_record(obs_payload, rrid, data.get("uuid") or run_uuid, start_time_ms)
                for key in keys:
                    try:
                        signed = self.request_obs_upload_url(object_key=key)
                        if signed.get("error") != 10000 or not isinstance(signed.get("data"), dict):
                            log("OBS上传: 失败 原因=凭证申请失败；成绩已提交，不要重复提交")
                            continue
                        info = signed["data"]
                        put = self.upload_to_obs(info["signedUrl"], info.get("actualSignedRequestHeaders") or {}, bound_payload)
                        if put.get("error") != 10000:
                            log("OBS上传: 失败 原因=对象上传失败；成绩已提交，不要重复提交")
                            continue
                        uploaded += 1
                        if self.verify_obs_object(key, bound_payload).get("error") == 10000:
                            verified += 1
                    except Exception as exc:
                        log(f"OBS上传: 未确认 原因={type(exc).__name__}；成绩已提交，不要重复提交")
            except (TypeError, ValueError) as exc:
                log(f"OBS关联: 失败 原因={exc}；成绩已提交，不要重复提交")
        else:
            log("OBS关联: 未确认 原因=结算响应缺少有效记录ID；不要重复提交")
        obs_uploaded, obs_verified = uploaded == 2, verified == 2
        log(f"轨迹状态: 上传={uploaded}/2 回读一致={verified}/2")

        # 6. Never turn missing detail or incomplete OBS verification into a pass.
        time.sleep(0.5)
        record_detail = None
        try:
            record_detail = self.history.get_one_record(rrid=rrid) if rrid else None
        except Exception as exc:
            log(f"详情回查: 未确认 原因={type(exc).__name__}；成绩已提交，不要重复提交")
        is_valid = record_detail.is_valid if record_detail and obs_verified else None
        
        # 跑后触发详情配置与看盘回刷
        try:
            self.get_rundetail_config(sport_type=sport_type)
            if self.unid:
                self.telemetry.send_page_exposure(unid=int(self.unid))
        except Exception as exc:
            log(f"页面刷新: 未确认 原因={type(exc).__name__}")

        label = "即时回查通过，待App复核" if is_valid is True else "即时回查未达标" if is_valid is False else "待核验"
        log(f"判定结果: {label}")
        return RunningSubmissionResult(
            success=True,
            rrid=rrid,
            error_code=10000,
            message="成绩已提交；最终状态需App复核" if obs_verified else "成绩已提交，但轨迹未完整确认；不要重复提交",
            total_distance=float(target_distance_m),
            total_time=int(target_time_sec),
            is_valid=is_valid,
            raw=sub_res,
            obs_uploaded=obs_uploaded,
            obs_verified=obs_verified
        )
