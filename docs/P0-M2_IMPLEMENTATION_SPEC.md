# 《三体星》P0-M2：四体引力积分与轨道演进引擎 — Codex实施规格

- 版本：0.1（面向 Codex 实施）
- 代码基线：`chenjunnan/threebodyplanet`，`main`，Git tree `7adb6f703aea0582ab77f84e501975d41de27920`（审查时）
- 范围：实现真实 Newtonian 4-body 轨道演进、运行中事件、诊断、结果归档与CLI；不涉及气候/3D/多人。

## 1. 已有接口与兼容底线

1. `src/threebody/world/models.py`：`WorldInitialState` 包含固定顺序的4个 `CelestialBody`，字段 `mass_kg/radius_m/position_m/velocity_mps`，SI单位、`BARYCENTRIC_INERTIAL`。不得破坏 M1 JSON 读取。
2. `src/threebody/integrator.py`：仅有 `NBodyIntegrator.integrate(initial_state, settings)->Any` 和 `IntegratorInputGuard.validate` Protocol。M2新增具体类型/结果结构，并保留向后兼容边界；优先将新的类型定义放入 `physics/nbody/`。
3. `src/threebody/config/models.py` 和 `config/loader.py`：目前明确禁止 `runtime.simulation_enabled=true`。应仅对有完备M2配置时解禁；不能让缺省M1配置被意外启用。
4. `src/threebody/experiments/runner.py` 生成每个候选的 `.initial.json`、`.screening.json`；`world generate`语义、输出格式和全部旧测试应保持不变。
5. `src/threebody/common/constants.py`：重用 `G=6.67430e-11`，严禁直接使用REBOUND默认 `G=1` 而不转换。
6. `screening/s0.py` 不强制正质量/半径，S1规则可关闭。M2求解器必须有自己的不可绕过输入守卫，不能以 `screening.decision=ACCEPTED` 替代。
7. `world/generators.py` 的 `hierarchical_orbit` 是采样近似，不声称长期稳定；M2真实演进必须直接使用输出的笛卡尔状态，不能按生成器轨道要素重建。

## 2. 建议开发目录（只新增必要模块）

```text
src/threebody/physics/nbody/
  __init__.py
  models.py          # IntegrationSettings/TrajectorySample/IntegrationResult
  guard.py           # mandatory solver input guard
  rebound_ias15.py   # REBOUND adapter
  diagnostics.py     # energy, momentum, angular momentum, barycenter
  events.py          # collision/encounter/escape observers
  runner.py          # integrate sequence, status and outputs
src/threebody/experiments/simulation.py # load M1 initial/screening and run M2
src/threebody/integrator.py              # retain protocol facade
configs/m2_ias15.yaml
schemas/simulation.schema.json           # if appropriate
...
tests/test_m2_guard.py
 tests/test_m2_integrator.py
 tests/test_m2_events.py
 tests/test_m2_cli.py
```

## 3. 输入和计算契约

- 使用 `WorldInitialState` 原始对象直接启动求解器；不变更其schema。用质量、半径、笛卡尔位置和速度构造4个 REBOUND particles；索引与规范ID相同。
- 必须明确 `sim.G = G` （SI），设置 `sim.integrator='ias15'`，并记录准确版本、epsilon、时间窗口、输出步长、事件配置与常数集。
- `initial.reference_epoch_s` 是积分起点，`t_start_s` 与 `t_end_s` 单调递增且有限；`output_interval_s>0`。生成的样本时间严格升序，含t_start，终点除碰撞提前终止外应恰为t_end；不足整除时要加入真正终点，不能越界。
- 默认只接受所有质量严格正且有限、半径非负且有限、所有位置/速度有限、无零距离点质量奇点、无物理表面重叠的初始状态；正半径可按配置决定是否属于硬性要求，但不能以禁用S1绕开求解器检查。
- `IntegratorInputGuard` 不依赖 S1 配置；S1被禁用只影响生成筛选，**不会**使非法积分输入通过。
- 不做任意的额外 `move_to_com` 来悄悄改变用户提供的初始数据（若必须转换，报告变换并保持可逆）。
- 不启用引力softening，除非将其作为单独、默认关闭、清晰标注的实验模型配置。

## 4. 模型及结果契约（建议）

`IntegrationSettings`：`backend: 'rebound_ias15'`；`start_time_s`、`duration_s`、`output_interval_s`、`epsilon`（可配置有理范围）、`collision.mode: 'terminate'|'record'`（P0仅实现terminate可接受）、`encounter.threshold_mode`与参数、`escape`诊断阈值、`max_samples`、`max_wall_seconds`（可选）；拒绝不认识的字段。

`TrajectorySample`：`t_s`；每个天体以规范ID记录 `position_m[3]`、`velocity_mps[3]`；`diagnostics`可选。

`IntegrationResult`：`schema_version`、`world_id`、`initial_state_sha256`、`integrator_name/version/settings`、`status: COMPLETED|COLLISION|STOPPED|FAILED`、`t_start_s/t_final_s`、`sample_count`、`event_count`、`artifact_refs`、`diagnostic_summary`。

文件：`runs/<id>/simulations/<candidate>/trajectory.jsonl`（或后续使用Parquet/NumPy，初期简单可靠）、`events.json`、`diagnostics.json`、`simulation_manifest.json`。写入原子提交/临时文件，失败不会产生“COMPLETED”假象。所有JSON禁止NaN/Inf。

**重要：** 内存中不应无限持有所有样本。批量预演要使用流式迭代/分块落盘，设置最大样本数保护。输出间隔只决定记录频率，不能强制积分器内部步长与之相同。

## 5. 事件与科学意义

### 5.1 碰撞
- 物理接触距离 `d_ij <= r_i+r_j`。默认`terminate`。采用REBOUND官方碰撞机制，验证时间与状态；不能只检查存盘采样点而漏掉两次采样之间的接触。
- 若REBOUND内置碰撞事件发生在积分内部步，则按照实际检测精度记录事件时间，不能声称无限精确的首次接触时间。对高速穿越/大步长边界增加专门测试；必要时做步内近接检测。
- 初始重叠属于Guard拒绝或专门事件策略；不能继续穿越重叠天体却称为真实碰撞。

### 5.2 近距离遭遇
- 记录两体最近距离，以及可配置阈值（米、半径倍数）；不改变动力学。
- 近接阈值“首次穿越”与最低距离的检测精度应区分；在P0可用求解器子步/事件回调或受控细化进行逼近。

### 5.3 逃逸
- `distance > R` **只能**作为“越过观察边界”，不能直接认定真正引力逃逸。
- 定义 `escape_candidate` 事件：相对系统剩余质心的距离、径向速度、两体近似比能等条件满足；明确为启发式分类，未来可增加持续时间判定。四体系统中不能将一次瞬时正能量判为永久逃逸。

## 6. 数值诊断

参考公式：
- `K = Σ 0.5*m_i*|v_i|²`
- `U = -Σ_{i<j} G*m_i*m_j/|r_i-r_j|`
- `E=K+U`（惯性系）；`P=Σ m_i*v_i`；`L=Σ m_i*(r_i×v_i)`；`R_cm`、`V_cm`。
- 对无碰撞、孤立四体系统，应报告 `|E(t)-E0|/max(|E0|, E_scale)`，其中 `E_scale = |K0|+|U0|` 防止E0≈0分母问题；另存传统相对能量误差仅在E0足够远离0时适用。
- 记录角动量矢量与稳定相对误差、线动量以及质心漂移。
- 一旦检测到碰撞并终止，诊断可给终止前的有限值；不要对合并或非保守过程使用孤立四体能量守恒指标。
- 所有科学阈值默认是示例验收容限，需要在固定测试案例上确认，不作任意混沌系统的通用保证。

## 7. 配置及CLI兼容策略

- `world generate --config ...` 必须保留原行为，默认不积分。
- 新增独立 `world simulate --initial <candidate.initial.json> --config <m2.yaml> --out-dir <path>`，第一版优先此方式，不要求先扩展旧生成器的配置schema。
- 如后续集成到原实验配置，新增**可选** `simulation`段并维持原样例、旧resolved config的哈希与校验兼容。不要直接删除 `runtime.simulation_enabled` 的守卫而不新增严格设置校验。
- 运行前校验 `WorldInitialState` 与输入guard；允许 `strict/research/raw` 都提交到M2，但Guard始终启用。
- 新CLI正确区分：配置错误(2)、非法求解输入(2)、求解失败(1)、完成(0)、预期碰撞终止（正常事件结果，退出码0）。

## 8. 实施序列

1. **M2-01 契约**：数据模型、配置、Guard、CLI help，测试旧数据往返。
2. **M2-02 REBOUND IAS15**：SI单位、四体积分、采样、结果；加入依赖（可设 `physics` optional extra，例如 `pip install -e '.[dev,physics]'`）。
3. **M2-03 诊断**：能量、动量、角动量、质心和误差报告。
4. **M2-04 事件**：碰撞提前终止、近距离遭遇、启发式逃逸分类。
5. **M2-05 归档/CLI**：模拟结果、配置和版本manifest；流式输出及失败状态。
6. **M2-06 验证**：完整单元/基准/CLI测试、文档、开发报告与决策记录。

## 9. 最低测试矩阵

- M1回归：`pytest -q`、`ruff check .` 原测试全部通过；旧 `world generate` 不变。
- 输入：负质量、零质量、NaN/Inf、负半径、重叠、同位置、非法单位/参考系，Guard都要处理；关闭S1不得放行。
- 二体圆轨道：从纯测试构造的4体案例中选定合理的弱扰动极限；或在底层Nbody模块允许测试用2体并与解析周期对照，不改变游戏世界4体契约。
- 真实四体：四个严格正质量粒子全部受力；行星质量改变恒星轨迹（不可默默作为无质量测试粒子）。
- 守恒：固定稳定基准下能量和角动量误差达预定容限，容限收紧后短期结果收敛。
- 碰撞：低速/高速接触、采样间发生接触、初始重叠；`terminate`输出真实事件及状态。
- 逃逸：高速外抛只标为候选，不声称永久逃逸；短暂越界/回归不应错判。
- 归档：JSON有效；输出时间严格单调；末尾样本与终止时间一致；异常中断不伪装完成。
- 科学：同初态+同环境/版本与设置在合理容差内复现；多配置与多样本数不改变初始状态生成流。

## 10. Codex指令

读取 `AGENTS.md`、`README.md`、`docs/03_DATA_CONTRACTS.md`、`docs/DECISIONS.md` 与本文件。创建 `feature/p0-m2-nbody` 分支，按M2-01到M2-06逐步实现。只作必要的兼容性修改，不重写M0/M1。运行全部测试，提供实际测试结果和演示命令；更新 `docs/development/M2_REPORT.md`、`docs/DECISIONS.md` 和README。提交PR供审查，不要自行合并到main。真实实验结果与已知精度/事件限制需要明确标注。不得使用轨道动画或固定轨道替代真实引力积分。
