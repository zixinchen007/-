# 轨迹关联与显示修复

## 数据流

1. 轨迹使用 `{"allLocJson":"JSON点列字符串","useZip":false}` 包装后压缩；地图序列化边界执行 BD-09 到 GCJ-02 转换。
2. 先结算一次取得有效记录 ID，再将 ID 绑定到轨迹对象。
3. 上传 UUID 小时路径和按记录 ID 分组路径两个对象，逐个申请 GET 回读并比较内容。
4. 区分成绩接受、轨迹上传、轨迹回读和即时成绩判定；缺少记录 ID 时不上传未关联对象，上传失败不再次结算。

状态解析综合明确的完成、拒绝标记与逐项规则结果；单独的 `status=0` 不直接等同未达标，未知状态继续保留为待核验。

## 跑道与单位

`route_loop` 或主 CLI 的 `--route-file` 可提供 BD-09 显式闭合路线。按官方点位顺序沿跑道行进；临近点位可用短支线连接，余下里程继续绕圈。拒绝自交、非法坐标、过远点位、距离不足等情况。路线文件在客户端网络调用前校验。

GPS 内部速度使用 m/s，地图配速使用 min/km，总览配速使用千分之一 min/km。距离、步数和统计窗口来自同一时间线。

## 验证

在当前目录运行：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -q
```

回归覆盖包装解码、坐标转换、先后顺序、ID 绑定、回读不匹配、部分上传、详情异常和状态判定。离线通过不代表手机 App 已最终显示或计分。旧记录不会因本地修复自动更新。

字段交叉参考：[Android DEX 格式](https://source.android.com/docs/core/runtime/dex-format)、[轨迹序列化](https://github.com/YanamiNeko/NekoSportsWorldTool/blob/e229abdbab71e377b86632e588e607413af4b93c/src/track/wire.rs)。原始资料与当前客户端版本不同，不构成官方兼容承诺。
