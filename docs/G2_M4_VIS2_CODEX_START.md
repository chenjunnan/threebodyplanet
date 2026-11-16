# G2 → M4 → VIS2：Codex开发迭代启动单

Repo: `chenjunnan/threebodyplanet`。
设计基线：`design/p0-g2-m4-vis2`（包含已经合并的M0/M1/M2/M3/VIS1）。

## 用户目标
三颗恒星+一颗岩石行星的质量与初始轨道参数自由随机：内双星尺度、外第三星尺度、行星轨道尺度、初始宿主、椭圆/双曲形状、离心率、倾角、相位与速度均由**可配置分布**采样；不把长期稳定、宜居、非碰撞作为默认准入门槛。REBOUND继续决定真正四体运动。M4从可用的动力学与温度历史推导环境事件与世界结局。VIS2用统一时间轴显示3D轨道、三恒星辐射、C0/C1温度、M4事件。

## 必读的权威规范（优先于AGENTS.md里历史里程碑限制）
- `docs/P0-G2_STOCHASTIC_WORLD_GENERATION.md`
- `docs/P0-M4_ENVIRONMENT_OUTCOME_SPEC.md`
- `docs/P0-VIS2_ORBIT_RADIATION_TEMPERATURE.md`
- `docs/P0-M3_IMPLEMENTATION_SPEC.md`
- `docs/P0-VIS1_TRAJECTORY_VIEWER_SPEC.md`
- `docs/DECISIONS.md`
- 现有`world/generators.py`、`config/models.py`、`screening/`、`physics/nbody/`、`physics/thermal/`和`visualization/`。

## 分支与PR策略（请按顺序执行）
1. 首先从设计分支切出 `feature/p0-g2-stochastic-worlds`；**先做G2**，完成后独立Draft PR指向`main`。只在G2中修改生成/配置/初态事件接口，保留历史算法配置解析、随机种子、config hash和fixture的行为。新分布和版本全部可追溯。
2. G2通过PR审查并并入main后，从新main建立 `feature/p0-m4-world-outcomes`，完成世界环境演化事件、分类、终止结局与批量统计，生成独立Draft PR。无足够物理模型支撑的“爆炸/海啸/完整大气”不要伪造。
3. VIS2可以在已合并M3+VIS1的main上另行开发 `feature/p0-vis2-linked-dashboard`，但必须兼容M4可选输入和G2初态事件输出；避免依赖尚未合并的私有分支。独立Draft PR。
4. 三个PR都不能自动合并。明确报告PR URL、提交SHA、测试与CI、可重复数据、样例模拟、已知限制与待审查点。

## G2硬约束
- 4体+SI+质心惯性系+全数值有限+可复现。
- 轨道所有关键尺度和相位可分布化；宿主选择权重可控；支持高偏心、双曲和速度扰动，区分“轨道元素”“扰动后瞬时状态”。
- 生成器不能屏蔽困难、逃逸候选或不宜居状态；仅S0与积分器数值安全不可禁用。
- 初始接触是合法游戏结局但是**不是合法普通IAS15初始条件**；单独归档终止事件，严禁关闭积分器输入校验。
- 真实碰撞后没有物理模型就必须停止计算，不要人为继续天体飞行。
- 旧world generate样本与现有M2/M3/VIS1全部回归。

## M4核心验收
- 动力结局 / 环境事件 / 游戏叙事必须分层，不能把候选逃逸误报为永久逃逸，也不能把恒星之间相撞误报成行星被毁灭。
- 可配置阈值的持续时间与暴露量计算需明确采样误差与时间覆盖，S0、数值安全与适居性完全独立。
- M2终止后不得继续给出伪温度；缺失热归档必须明确。
- 使用真实M2/M3实验样例以及纯测试夹具做不同结果分类的单元与端到端验证。

## VIS2核心验收
- 同一时间轴驱动3D星体位置、各星/总通量、C0全球温度、C1纬度场与事件。
- 明确标注物理和显示上的插值；碰撞时停止，有逃逸候选时继续移动（如果真实数据继续）。
- 可离线HTML、安全事件文本、大规模数据限制、时间与hash对齐，保留VIS1原有CLI行为。

## 质量门槛
```bash
pytest -q
ruff check .
python -m compileall -q src tests
```
所有脚本和CI结果必须真实报告，不能凭空宣称通过。每个里程碑更新`docs/development/G2_REPORT.md`、`M4_REPORT.md`或`VIS2_REPORT.md`。

## 当前阶段明确目标
**从G2-01开始真实编码，不要仅写设计文档。** 先交付新的严格配置模型、数值采样器、轨道元素到SI笛卡尔状态的科学基准测试、随机宿主与历史回归用例。然后继续完成G2直至可提交Draft PR。
