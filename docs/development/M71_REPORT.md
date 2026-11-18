# M7.1 基线、随机行星与地面观测开发报告

日期：2026-11-18。开发分支 `feature/p0-m71-web-atmosphere`，基线 main `cbecc75`。
本报告记录实际实现与测试，不把随机先验或视觉近似称为观测。

## 基线与 Web 核心

- 包版本更新为 `0.7.1`，M5A/M3/M4 等归档 schema 保持原版本；M7 场景显式升级为
  `planet-exploration-v2`。
- `AGENTS.md`、总体路线图和 M7 合并状态已同步；浏览器 E2E 与 M7 参数科学工作流增加
  相关 `main` push 触发。
- 新增 `web-core.js`，统一 JSON GET/POST、共享状态及路由进入/离开生命周期。任务仍由
  本机服务端状态机和物理时间控制；路由隐藏会停止地表/轨道播放，不暂停积分器。

## 0.4.3 随机行星

- `planet_rotation.period_s` 为严格 SI 分布，自转轴为各向同性；使用 720/721 独立子流。
- `retention_weighted_prior_v1` 使用质量、半径、旋转修正逃逸速度、初始三恒星辐照、
  平衡/高层温度代理和平均分子质量形成 Jeans 参数代理。
- 大气是否出现还受显式发生率和 PCG64 抽样控制。成分由五气体 Dirichlet 参数生成，
  高温/低压下水汽受粗略饱和分压上限约束，默认氧权重很低。
- 0.4.2 的候选 0/1/7 世界 JSON SHA-256 已冻结回归，证明新子流没有改变历史结果。

## 纬向风、地形与光学

- 36 个等面积纬带输出热力/地转参考尺度约束的随机风速上下界；真空返回 null，
  上界限制为局地参考声速的 0.3 倍。
- 程序地形由 12 个球面模态和显式形状指数生成，默认起伏 18 km，仍受半径 5% 上限；
  全球地图绘制 24 个高程级别的等高线。
- 地面镜头仅沿精确已归档采样播放，可锁定地平线上方的任一恒星。
- 680/550/440 nm 三波段代理计算成分/气压 Rayleigh 与宽带吸收、空气质量、恒星直达
  辐照和天空相对颜色。恒星本征颜色来自归档光度/半径的黑体有效温度代理，WebGL 最终
  使用 HDR 色调映射；像素不是 SI 辐亮度。

## 实际测试

- `pytest --override-ini addopts='' -q`：**259 passed in 34.87s**，Python 3.11.18，包含
  4 项真实 Chromium 测试、回环 Web API、REBOUND IAS15、M3/M4 及全部历史回归。
- Chromium：使用本机 Playwright Chromium，验证新地面采样前进/播放、恒星锁定、
  `planet-exploration-v2`、24 级等高线、风范围和大气光学。
- `ruff check .`：通过。
- `python -m compileall -q src tests`：通过。
- 所有 `web/static/*.js` 执行 `node --check`：通过。
- wheel：`three_body_planet-0.7.1-py3-none-any.whl`，SHA-256
  `dbb5f9149c6264eb218a9aca4d014cf7d0fa3dc5f55fbf603f5737bb57ce9f71`。

本机 shell 继承 ROS Python 3.10 pytest 插件时会在收集前缺少 `lark`；使用已存在的
`threebody-py311` 环境并设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 后消除外部插件污染。
这不是项目测试失败。

M5A 二进制检查点按既有安全策略要求 `project_version` 精确匹配，因此 0.6.0 进程留下的
暂停检查点不会由 0.7.1 静默恢复。其已提交轨迹、事件及完成归档仍可读；需要继续旧暂停任务时
应使用原 0.6.0 环境，不能伪装版本绕过检查。

## 真实短链路验收

使用 `configs/m71_random_planets.yaml` 的候选 0，执行真实 60 秒 IAS15、10 秒输出并在
精确 `t=60 s` 构建场景。积分状态 `COMPLETED`；行星周期 542497.5646 s，大气先验状态
`PRESENT_PRIOR_SAMPLE`，场景压力 1.87152949e7 Pa，36/36 纬带产生风范围，24 级等高线，
光学模型为 `three_band_rayleigh_absorption_proxy_v1`。完整来源/场景哈希保存在
`docs/acceptance/m71/real-short-run.json`。

该单个短运行只验证链路和契约，不证明长期轨道稳定，也不校准大气、风或地貌真实性。

## 已知限制

- Jeans 参数、发生率和 Dirichlet 成分是可复现先验，不含形成史、恒星风、光化学、
  火山逸气、撞击剥蚀或随时间积分的大气逃逸。
- 风范围没有质量/动量方程、边界层、经度、地形摩擦或天气。
- 三波段光学没有云、气溶胶、折射、多次散射、谱线或地形遮挡；HDR 曝光是显示变换。
- 地形没有板块、岩性和侵蚀。24 指高程级别，不代表全球只有 24 个闭合轮廓。
- 地面播放每个画面都使用真实采样，但采样之间没有插值；播放速度不等于积分速度。
