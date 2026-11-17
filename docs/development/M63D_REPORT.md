# M6.3-d 表面材质与 C1 温度球面

基线：main 7bc8838（已合并 M6.3-c），2026-11-17。

## 实现

- 新增本地 surface-materials.js，版本 surface-v1；三种恒星颗粒与岩石纹理使用确定性三维噪声生成，缓存复用，无 CDN 或纹理图片下载。
- Canvas 与 WebGL 共用材质源；可关闭材质恢复简单符号。WebGL 点精灵使用透明圆形球面贴图；轨迹线不贴图，销毁时释放 GPU 纹理。
- 热模型区增加 C1 温度球面，用正交投影显示原始温度纬带，验证等面积网格及非负有限温度。色标与现有 C1 时空图一致，窗口内固定，等温与微小温差有说明。
- 选择最近真实 M3 采样，显示时间差及质量警告；悬停显示纬带中心、K 与 °C。不在归档时间范围外取温度。
- 读取轨道失败、空轨迹或无有效热数据时清除温度球面与色标。
- 未修改物理积分器、科学归档格式、M3/M4 算法。材质不驱动物理，自转及地形不从纹理反推。

## 验证

云环境 Python 3.12 + 系统 Chromium：

- 完整测试：248 passed（包括 3 项真实浏览器测试）。
- 浏览器覆盖：真实 IAS15→M3/M4→球面、材质开关、WebGL shader/纹理错误检查、点选及旋转。
- 图形夹具覆盖：两条等面积纬带南北颜色、透明圆外、K/°C 悬停、采样时间差、质量警告、无效网格/NaN/缺失/越界拒绝、清空旧画面及原始输入不变。夹具不是科学计算结果。
- Ruff、JavaScript 语法、diff whitespace 检查和 wheel 构建；包内包含 surface-materials.js，并加入浏览器 CI 的包资源校验。
- 手动检查 Chromium 材质预览及温度球面截图。

复测命令：

```
THREEBODY_TEST_CHROMIUM=/usr/bin/chromium .venv/bin/pytest -o addopts='' -q
.venv/bin/ruff check .
node --check src/threebody/web/static/surface-materials.js
node --check src/threebody/web/static/webgl-observer.js
node --check src/threebody/web/static/app.js
```

## 科学与显示边界

轨道材质使用增强尺寸的球面贴图符号，不是物理半径网格。恒星色彩是版本化视觉配色，不代表真实光谱。
岩石纹理不是已验证地形，也不表示海洋、大气或生命。球面明暗为固定装饰光照，不表示三恒星真实照明。
C1 球面独立展示在行星局部坐标中，北纬朝上；尚未实现与归档自转轴对齐的轨道网格。
温度图不添加装饰明暗，不推断经度天气或日夜。五年验收没有重跑，本次未改科学求解器。
下一增量 M6.3-e 为碰撞/事件视觉与性能基准，仍待实施。
