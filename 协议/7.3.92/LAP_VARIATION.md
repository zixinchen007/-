# 多圈走线微变

在显式闭合路线外侧产生连续的 0–2 米偏移。每 50 米设置控制值，相邻变化不超过 0.5 米，使用平滑插值。官方点位仅强制按顺序访问一轮，附近连接区域收敛到原路线。

```powershell
.\preview.cmd --lap-variation --distance 2537 --time-sec 1183 --seed 42
.\preview.cmd --motion-model v3 --lap-variation --distance 5000 --time-sec 2401 --seed 42
```

未传路线文件时使用虚构跑道。可传入自己的 `--route-file`、`--checkpoints-file` 和带来源日期的 `--fences-file`。缺少围栏快照时明确显示未验证；提供时检查整条线段和多边形并集，不只检查端点。

## 接口

- 离线入口支持 `--lap-variation`，主 CLI 没有这个参数。
- 在线 Python 门面的 `execute_run(..., route_loop=..., motion_options=..., route_variation=RouteVariationOptions())` 显式启用微变，默认关闭。
- 正偏移要求显式跑道和运动时间线；零偏移保持既有路线行为。
- 路线随机序列独立派生，保留固定点位置及元数据；拒绝自交、内侧越界、点位不可达和异常采样规模。
- 连续慢跑模型的速度时间线与路线微变分开；V3 先准备最终路线，再按该路线曲率求解，因此路线变化可能改变运动曲线。

预览包含分圈着色、叠加与局部观察视图，数据默认位于 `.local/previews/`。围栏快照不能代替实时规则查询。本仓库不附带真实路线、围栏或个人运行记录。
