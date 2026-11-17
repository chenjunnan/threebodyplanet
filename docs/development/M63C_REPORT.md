# M6.3-c observer-controls 分支集成记录

2026-11-17：将 main add84d5（已合并 PR #30、#31）合入旧 observer-controls 分支。
原 51fa105 与 main 上另一套 M6.3-c 实现重叠，不能直接以旧界面覆盖 main。

## 冲突解决

保留 main 的哈希核验天体元数据 API、科学适配器、选择/聚焦面板、事件时间联动，
以及 M6.3-d 本地材质、WebGL 纹理、C1 温度球面、色标和浏览器测试。
将旧分支的独有功能适配进 main 现有接口：

- Canvas/WebGL 画布键盘旋转、缩放、重置和历史回放。
- 切换观察器保留相机角度、缩放、选择、聚焦与当前采样；实际 WebGL context loss 自动绘制 Canvas。
- Canvas 点击天体复用现有选择及真实属性面板，不增加第二套选择 UI。
- 销毁 WebGL 时移除全部实例监听器，保留材质纹理释放；反复切换不重复响应拖动。
- WebGL 宽高比投影与点选变换一致；渲染原点/聚焦偏移以双精度相减后转换 GPU 单精度。
- 失败轨迹读取清除时间标签/滑块，沿用 D 的温度数据清空。

物理求解器、归档格式、M3/M4 算法没有修改。没有自动合并到 main。

## 验证

```
THREEBODY_TEST_CHROMIUM=/usr/bin/chromium .venv/bin/pytest -o addopts='' -q
.venv/bin/ruff check .
node --check src/threebody/web/static/app.js
node --check src/threebody/web/static/webgl-observer.js
```

浏览器回归覆盖真实 IAS15→M3/M4→温度球面/报告、材质开关和 GPU 纹理、
新旧控制集成、反复切换相机状态、单次拖动响应、实际 context loss、Canvas 点选、
键盘回放停止于真实样本末尾及源数据不变。纯图形夹具不视为科学积分结果。

本次结果：248 项测试全部通过（30.59 秒，含 3 项真实 Chromium 测试）；Ruff、
JavaScript 语法及冲突标记/空白检查通过。
