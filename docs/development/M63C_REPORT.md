# M6.3-c 相机、点选与真实采样回放

日期：2026-11-17。基线：main 47871a2（M6.3-a/b 与五年验收已合并）。

## 交付

- Canvas/WebGL 共用 yaw、pitch、zoom 与聚焦目标；按钮、鼠标及键盘均操作当前观察器。
- 点击当前帧天体或选择下拉项聚焦，详情显示原始惯性系 SI 坐标及真实采样时间。
- 回放逐点遍历当前窗口已经验证的轨迹，并停在最后一点；不插值、不外推。
- WebGL 按画布宽高比投影；点选使用同样的透视变换。双精度减去渲染原点、聚焦偏移之后才转换 GPU 单精度。
- 切换渲染器保留相机/所选时间；销毁时移除 context-loss 监听器，重复启用不会累积拖动监听器。
- 实际 WebGL context loss 回退 Canvas；轨迹读取失败清除旧位置、温度游标与选择。
- 没有修改求解器、归档 schema、科学数据及科学分类。

## 验证

当前云环境 Python 3.12、系统 Chromium，通过：

```
THREEBODY_TEST_CHROMIUM=/usr/bin/chromium .venv/bin/pytest -o addopts='' -q
# 246 passed in 25.70s（含两个真实浏览器测试）
.venv/bin/ruff check .
node --check src/threebody/web/static/app.js
node --check src/threebody/web/static/webgl-observer.js
```

浏览器真实科学链路覆盖世界生成、IAS15、M3/M4、Canvas 聚焦及报告。
软件 WebGL2 测试使用明确标注的图形夹具，覆盖 shader、鼠标旋转、滚轮、键盘、
按钮、点选、聚焦居中、回放终点、重复开关、实际 context loss、缺失轨迹清空以及源坐标不变。
图形夹具不是科学计算结果。CI 默认仍用 Playwright 管理的 Chromium；环境已有 Chromium
可通过 THREEBODY_TEST_CHROMIUM 指定。当前浏览器下载域名被网络策略拒绝，使用已有系统浏览器完成测试。

## 剩余范围

M6.3-d 恒星材质及 C1 球面热图、M6.3-e 事件视觉与性能基准尚未实现。
本增量不重跑五年科学验收（未改科学计算）；已有五年验收记录参见 docs/acceptance/。
WebGL 仍为实验选项，不支持在 GPU 上推进物理状态。
