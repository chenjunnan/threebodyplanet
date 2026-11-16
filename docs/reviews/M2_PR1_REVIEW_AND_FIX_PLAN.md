# 《三体星》P0-M2 PR #1 技术审查及修复计划

审查时间：2026-11-16  
仓库：`chenjunnan/threebodyplanet`  
PR：`https://github.com/chenjunnan/threebodyplanet/pull/1`  
审查提交：`002d068e70bddee5793f5a0eb116f3ac35c9b7b7`  
基线：`7adb6f703aea0582ab77f84e501975d41de27920`

## 结论

**REQUEST CHANGES：暂不合并。** 核心四体积分器与M0/M1兼容性方向正确。需先修复碰撞归因并增加墙钟超时的真实集成测试；补全CI后再合并。此结论属于源码静态审查，尚未独立复跑全部pytest。Codex提交的报告记录了55项pytest通过、ruff通过、一年模拟367个采样点。

## P1 — 碰撞双方归因错误（必须修复）

位置：`src/threebody/physics/nbody/events.py:EventObserver.collision_event()`；调用点 `src/threebody/physics/nbody/rebound_ias15.py:integrate_stream()`。

现状：当REBOUND抛出`Collision`后，程序扫描所有天体并选取最终状态中表面间隙最小的一对作为事件参与者。`collision="line"`是基于积分步内线性扫掠轨迹检测碰撞；触发扫掠碰撞的天体不一定是步末位置最接近的一对。结果可能误报碰撞双方，破坏事件因果。

修复：改用自定义`collision_resolve`回调，读取REBOUND提供的`collision.p1`、`collision.p2`，捕获真实碰撞粒子索引并停止积分；不要依赖最终位置反推出碰撞双方。确保回调对象在积分完成前保持引用。建议保留碰撞时刻的积分器内部步精度说明，且在回调内不直接改动粒子位置或速度。明确半径为零时的检测边界。

验收：构造两对粒子，其中触发扫掠的A/B在内部步末并非最近对，但另一个C/D间隙更小；检查最终事件归因为A/B。检查四体顺序、散列ID、碰撞后状态和终止码，并复测高速穿越输出采样间隔的案例。

参考：https://rebound.hanno-rein.de/collisions/ （官方collision resolve回调提供`p1/p2`），以及REBOUND C源码中的`reb_collision_resolve_halt`不记录触发粒子信息。

## P1 — 真实墙钟超时路径未被验证（必须修复或证明正确）

位置：`src/threebody/physics/nbody/events.py:EventObserver.heartbeat()`和`rebound_ias15.py:integrate_stream()`。

现状：heartbeat触发`simulation.stop()`，但积分器没有明确按REBOUND版本验证该调用的返回/异常语义。`except Exception`可能把正常用户停止包成`IntegrationExecutionError`，最终归档为FAILED，而非预期STOPPED。现有测试没有覆盖实际REBOUND超时停止路径。

修复：设置一个可确定触发的极短时限或可注入的单调时钟，并在集成测试中真实运行IAS15；验证最终状态、终止时间、状态码与manifest一致。按REBOUND实际行为实现明确状态转换，不能简单吞掉所有异常。超时触发应保留部分有效采样与已观察到的事件，并有一致的`STOPPED`语义。

验收：实际超时案例得到`STOPPED`（或如选择其他语义须在规范中一致且显式），CLI exit code、归档manifest、`t_final_s`与轨道最后采样时间一致；没有`COMPLETED`误报。

参考：https://rebound.hanno-rein.de/simulation/timestepping/

## P2 — 初始物理守卫的数值安全边界（建议本轮补齐）

位置：`physics/nbody/guard.py`和`diagnostics.py`。

当前输入守卫检查finite/正质量/初始重叠，但有限的极端质量与距离仍可能造成加速度和总能量上溢、距离范数溢出，进而导致科学诊断的NaN/Inf。增加最小可计算距离、力学量有限性检查与清晰的输入错误；不得将这些加入可选的S1规则。避免制定与世界可玩性混淆的硬性数值阈值：可通过实际加速度、势能与求解器浮点范围判断。

验收：极端但有限的输入不会静默输出含NaN/Infinity的结果；应被明确拒绝或标记为FAILED，且能说明缘由。

## P2 — 近距离事件与最小距离指标语义（记录并可延后精化）

`EventObserver`只检查heartbeat中的内部步端点与初态；近距离阈值瞬时跨越可以漏检，报告的minimum distance也是采样最小值，不是连续轨道的数学最小值。现有报告已经说明局限。建议字段改为`minimum_observed_pair_distance_m`或增加`detection_resolution=internal_step_endpoint`，避免下游游戏逻辑将其作为严格最小距离。后续可增加近距离事件的区间括定和局部重积分算法。

## P2 — P0工程质量与测试门禁

PR的开发报告记录了55项通过测试，但GitHub PR head没有可见的CI check runs或commit statuses。添加GitHub Actions：Python 3.11 + 3.12、安装`.[dev,physics]`、`pytest -q`、`ruff check .`；标记REBOUND物理测试不应因为缺少依赖而自动跳过。保留当前M0/M1回归测试。更新M2_REPORT报告中的最终测试总数、具体REBOUND版本、测试环境和限制。

## M2修复阶段的Codex任务顺序

1. `M2-R1` 修复碰撞事件回调归因，新增复现测试。
2. `M2-R2` 验证和修复墙钟超时状态机，新增真实积分集成测试。
3. `M2-R3` 增加极端有限输入与诊断NaN防护测试。
4. `M2-R4` 明确近距离报告语义。
5. `M2-R5` 增加GitHub Actions CI，更新README、DECISIONS、M2_REPORT。
6. 提交到现有PR #1分支，不新建平行PR；等待复审，**不要自行合并**。

## Codex启动指令

请在现有`feature/p0-m2-nbody`分支、PR #1上完成M2审查修复。先阅读`AGENTS.md`、`docs/P0-M2_IMPLEMENTATION_SPEC.md`、`docs/development/M2_REPORT.md`以及本文件。按M2-R1至M2-R5依次实现，不重写M0/M1，不改变`WorldInitialState`的0.1.0结构和`world generate`语义。碰撞必须使用REBOUND真实回调提供的`collision.p1/p2`而非基于终止状态的最近距离猜测；实测墙钟超时语义；明确诊断数值安全；增加可强制运行的CI。运行完整测试并记录结果，推送至现有PR #1，等待审查，不自动合并。

## 下一阶段

M2通过门禁后进入`P0-M3`：三恒星辐射、行星自转、C0零维能量平衡与C1纬向能量平衡。M3不涉及真实大气动力学或完整天气模拟。输入沿用`WorldInitialState`和M2轨道序列，严格分开物理辐照度与视觉光照。M3首个目标是从任意时刻的四体位置计算行星所受多恒星辐射通量，并验证距离平方反比、光度叠加和热惯性响应。
