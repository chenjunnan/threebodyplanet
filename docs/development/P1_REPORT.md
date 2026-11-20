# P1 开发报告

状态：开发中，尚未达到最终 PR 验收，不得视为完整交付声明。

## 当前已实现

- 基于已合并 M7.2/M7.3 的 main 创建 `feature/p1-3d-planet-exploration`。
- 双引擎 Chromium 实测并选择 Babylon.js；`/game/` 与科学控制台独立。
- M5A 天体/轨道、M7.2/M7.3 点查询及 `planet-exploration-v9` 地形适配。
- 5×5 个 4 km 连续地形块、第一/第三人称、移动、奔跑、跳跃、归档重力和 HUD。
- 三星 `2 asin(R/d)` 角直径、辐照度曝光、行星自转姿态。
- 温度、压强、湿度、风、云与降水展示；缺失数据明确不可用。
- 存档绑定 run ID 和初态 SHA-256。

## 已执行验证

- `npm run build`：通过。
- P1 Python 路由/契约：3 passed。
- 变更 Python `ruff check`：通过；`compileall`：通过。
- 两引擎各 360 个计量帧，均无 page error。

## 尚未完成

动态四叉树 LOD/浮动原点重定位、云雨雪粒子、相态材质、真实归档游戏 E2E、长距离跨块
碰撞、桌面 GPU 性能、完整 `pytest -q` 和最终 PR 仍待完成。当前实现不能描述为已满足
P1 最终验收。
