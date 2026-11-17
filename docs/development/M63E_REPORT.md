# M6.3-e 事件视觉、事件跳转及图形基准

2026-11-17，基线 main 5f0ec78（已合并 M6.3-c/d）。

## 交付

- 动力学事件适配器核对运行/世界、事件数量、时间顺序、类型和规范天体 ID，拒绝轨迹延伸到碰撞后。
- 碰撞归档持续显示终止提示；自动选中可在当前窗口定位的碰撞事件。
- Canvas/WebGL 同步静态事件圆环：碰撞粉色，其余选中事件青色；用户可关闭，无闪烁或伪碰撞后演化。
- 事件标记仅在最近真实采样处显示，文字给出事件时刻、采样时刻和偏差；不声称该位置是精确接触位置。
- 上/下事件跳转会暂停历史播放；窗口外事件禁用跳转，不插值、不外推；读取失败清空事件和终止提示。
- Canvas 缓存固定窗口中心/范围；WebGL 每个天体独立缓存轨迹 buffer，窗口或聚焦原点不变时避免重复上传，聚焦偏移继续双精度相减后转 GPU 单精度。
- 新增 scripts/m63_observer_benchmark.py；浏览器 CI 运行短基准并上传 JSON。

## 验证

```
THREEBODY_TEST_CHROMIUM=/usr/bin/chromium .venv/bin/pytest -o addopts='' -q
.venv/bin/ruff check .
node --check src/threebody/web/static/app.js
node --check src/threebody/web/static/science-adapter.js
node --check src/threebody/web/static/webgl-observer.js
THREEBODY_TEST_CHROMIUM=/usr/bin/chromium .venv/bin/python \
  scripts/m63_observer_benchmark.py --frames 120 --out /tmp/m63-new-report.json
```

浏览器新增真实 REBOUND 碰撞归档验收：两星实际碰撞、终止时刻与最后采样一致、
Canvas/WebGL 标记开关影响图像、历史回放停在最后采样、源坐标未改变、坏运行清空旧状态。
纯图形检查覆盖近接标记、采样时间偏差、越界事件、跨运行 ID、非有限时间、未知天体、
碰撞后采样拒绝；并强制模拟 WebGL2 不可用以验证 Canvas 回退。
原有真实科学流程、材质/温度球面、相机切换/context loss 和报告下载验收保留。

完整结果：249 passed（34.11 秒，含 4 项真实浏览器测试）；Ruff、JavaScript 语法、
空白检查与 wheel 构建通过。

## 本机基准结果

原始结果：`docs/benchmarks/m63e-software-chromium-2026-11-17.json`。
系统 Chromium 156，Linux，强制 SwiftShader 软件 WebGL2；4 天体、2000 个输入采样、
940×500 画布、材质开启。每个模式预热 10 帧，再测 120 帧；回放使用最后 120 个采样。
这是明确标记的图形夹具，不是物理积分，不测科学求解、热面板或 API 查询。

| 模式 | 绘制耗时 p95 (ms) | 浏览器帧间隔中位数 / p95 (ms) |
| --- | ---: | ---: |
| Canvas 静态 | 1.9 | 83.3 / 100.1 |
| Canvas 回放 | 1.7 | 83.3 / 133.2 |
| WebGL 静态 | 0.3 | 16.7 / 33.3 |
| WebGL 回放 | 0.3 | 16.7 / 33.3 |
| WebGL 聚焦回放 | 0.9 | 16.7 / 33.3 |

Canvas 耗时只计 API 提交，不能当作完成光栅化的耗时；WebGL 包含 gl.finish()。
帧间隔同时受浏览器调度和渲染影响。该软件环境下 Canvas 未达到 60 FPS，WebGL 中位数
接近 60 Hz，但 p95 有掉帧，也不满足稳定 60 FPS；本增量不宣称该性能目标已全部达成。
计时不是普遍硬件性能保证，没有以阈值禁用测试或隐藏结果。所有模式均核验源数据未变。

## 科学边界

没有改变物理积分器、事件检测算法、已发布归档 schema、M3/M4 或碰撞后行为。
圆环是位置提示，不是光晕能量、爆炸半径、合并残骸或碰撞后轨迹。逃逸仍为候选。
五年科学积分没有重跑。本增量覆盖 e 的事件与基准交付；硬件 GPU/移动端性能、
热面板整体帧率及更严格的帧预算调优仍需后续测量。
