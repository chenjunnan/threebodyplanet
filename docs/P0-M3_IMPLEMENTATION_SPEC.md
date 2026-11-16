# P0-M3 — 多恒星辐射、行星自转与 C0/C1 热力学模型实施规格

状态：Proposed · 基线：`main`（已包含 M2 PR #1） · 面向 VS Code Codex · 版本：M3-design-0.1

## 1. 目标、边界与严格要求

M3 读取 M2 的真实四体轨道结果（`trajectory.jsonl`、`simulation_manifest.json`、`*.initial.json`），计算三恒星辐射强迫、行星地表入射辐射、C0 全球平均温度和 C1 纬度分带温度。未来 C2 天气与 Web 渲染可消费同一辐射数据，但 M3 不包含完整天气、海洋、云、地壳、玩家或游戏服务器。

- 不修改 M2 的 Newtonian N-body 力学，不把气候反馈到轨道。
- 严格采用 SI：W、m、s、K、J/m²/K、W/m²/K；`t_s`与 M2 处于同一模拟历元。
- 不改变 M0/M1 `WorldInitialState` v0.1.0 或 M2 `TrajectorySample`、`IntegrationResult` v0.2.0 的旧字段含义。
- M3 的所有旋转与热参数由单独的、严格验证的 `m3.yaml` 提供；可选的行星 properties 映射需要明确版本化，不得凭空假设已存在。
- 默认使用 Python + NumPy + SciPy 实现**科学参考模型**。C++ 原生模型仅预留数据与接口契约；本次不迁移 REBOUND，不复制两份难以验证的计算逻辑。
- 不使用预设灾害、虚假太阳轨道或随意振荡温度代替物理计算。
- 不将 C0/C1 结果称为真实局部天气预报或宜居性的充分证明。

## 2. 源码现状与兼容契约

已存在：
- `src/threebody/world/models.py`：`WorldInitialState`，四个有序 `CelestialBody`，`mass_kg`、`radius_m`、`position_m`、`velocity_mps`、`properties`。
- `src/threebody/world/generators.py`：恒星 `properties["luminosity_w"]` 来自近似主序星质量—光度关系；不是经过精确恒星演化计算的光谱模型。
- `src/threebody/physics/nbody/models.py`：`TrajectorySample` 具有 `t_s` 和四个 `BodyTrajectoryState`。
- `src/threebody/experiments/simulation.py`：原子归档输出轨道、事件、诊断、manifest。
- `src/threebody/cli.py`：`threebody world generate` 与 `threebody world simulate`。
- REBOUND IAS15 是 M2 的唯一权威轨道积分器；M3 不接管世界物理时间。

新增：`src/threebody/physics/stellar/`、`src/threebody/physics/rotation/`、`src/threebody/physics/thermal/`、`src/threebody/experiments/thermal.py`、`configs/m3_thermal.yaml`、`schemas/thermal.schema.json`、`tests/test_m3_*.py`。
避免修改 M1 预置配置和 M2 结果 Schema。若必须修改依赖文件，写 ADR 解释兼容性与回归测试。

## 3. 最小物理模型

### 3.1 多恒星辐射强迫

对每颗恒星 i，行星中心接收的辐照度：
`F_i(t)=L_i/(4π d_i(t)^2)`，其中 `d_i=|r_star_i-r_planet|`。
总 `F_total=Σ F_i`；每颗星独立保存 `distance_m`、`direction_inertial_unit`、`flux_w_m2`。
如果恒星中心到行星中心的距离不大于 `R_star+R_planet`，拒绝继续辐射求解并标记物理失效；不可忽略 M2 的碰撞终止时间。

先采用各向同性点光源、波段积分的总光度；**M3 不处理恒星间遮挡、日食、有限星盘、温室气体和真实光谱输运**。输出 `model_assumptions` 明确这些限制。

要求各恒星 `luminosity_w` 严格为正且有限，禁止为缺失光度静默赋默认值；未来 `stellar_profile` 可以实现质量—光度关系，但必须版本化。

### 3.2 行星自转与球面法向

M3 配置定义行星自转轴单位向量 `axis_inertial_unit`、初始本初子午面方向 `prime_meridian_inertial_unit`、自转角速度 `rotation_rate_rad_s`（可以正、负或零）以及 `rotation_epoch_s`。
对 `axis` 和 `prime_meridian` 作严格归一、正交校验（不能静默改变输入），构建右手正交基 `e1, e2, axis`。
角度 `theta=rotation_rate_rad_s*(t-rotation_epoch_s)+initial_phase_rad`。当地 `latitude_rad` / `longitude_rad` 法向：
`n=cos(lat)[cos(lon+theta)e1+sin(lon+theta)e2]+sin(lat)axis`。
对恒星方向 `s_i`，局地顶层大气入射强迫 `I(lat,lon,t)=Σ F_i max(0,dot(n,s_i))`。

严格区分：
- `planet_center_flux_w_m2`：行星中心外部辐照度，没有除以4；
- `top_of_atmosphere_local_w_m2`：局地球面法向投影，不等于吸收值；
- `globally_averaged_incoming_w_m2 = F_total/4`：理想球体、无日食、点源条件下；
- `absorbed_w_m2=(1-albedo)*incoming_w_m2`。

自转角精度：极大模拟历元应考虑模角时的数值精度；必须报告相位参考。转轴暂固定，不计算潮汐力矩、自转衰减或进动。

### 3.3 C0 全球能量平衡

`C_A dT/dt = (1-A)*F_total(t)/4 + Q - ε σ T^4`。

定义 `C_A>0` 单位 `J m^-2 K^-1`，Bond 反照率 `0≤A≤1`，有效发射率 `0<ε≤1`，`Q`（内部热通量）单位 `W m^-2`。所有参数以配置明确给出。
`T(0)>0` K；不允许负Kelvin温度或数值非有限值。
建议以 SciPy `solve_ivp` 自适应积分作为基准；允许准确的自实现步进法，但必须通过收敛与能量收支测试。给出 `rtol` / `atol_k`、`max_step_s`、轨道插值误差监控。**采样间隔不是积分步长**。
要记录积分能量残差：`C_A*(T_end-T_start) - integral((1-A)F/4+Q-εσT^4)dt`，以 `J/m²` 输出及归一化指标。
定常单星基准：`T_eq=[((1-A)*F/4+Q)/(εσ)]^(1/4)`（根号内严格非负）。对于 `F=1361 W/m²`、`A=0.3`、`ε=1`、`Q=0`，期望约 255 K，仅为无温室简化平衡温度。

### 3.4 C1 纬向能量平衡

C1 采用 `x=sin(latitude)` 等面积网格，`N` 可配置（初期建议 18 或 36）：
`C_A(x) ∂T/∂t = (1-A(x))*I_bar(x,t)+Q(x) - ε(x)σT^4 + ∂/∂x[D(1-x²)∂T/∂x]`。
`D` 单位 `W m^-2 K^-1`；纬向扩散通量须采用**有限体积、极点零通量边界**，保证无外部强迫时经面积加权积分的水平热输送总量为零。
推荐对扩散采用稳定的隐式处理或SciPy BDF；请做分辨率与步长收敛检查，不能使用会在时间放大时不稳定的显式大步扩散。
纬度平均日照 `I_bar` 的定义必须明确：
- M3 C1 默认采用给定时间点的**经度积分平均** `1/(2π)∫I(lat,lon,t)d lon`，不代表真实某条经线的瞬时天气。
- 该平均可以对每颗恒星独立求解析日照积分或使用周期化数值求积验证，必须覆盖极昼/极夜。
- 对刚性球体，完整纬度面积加权平均应回归 `F_total/4`。在全球均匀初始条件、相同参数且 `D=0` 时，应与C0总能量约束一致，但纬度温度会因非线性热发射产生差异。
- 经度积分的结果只依赖恒星与自转轴夹角，不应错误地随本初子午线相位变化；局地瞬时日照则会随自转变化。
- 强迫随轨道变化，不能只在整个模拟期间取单个平均值。

**注意**：经度平均模型不能捕获昼夜温差、经向风和移动风暴；未来C2需要经纬度和动力学大气场。

### 3.5 M2轨道读取与时间插值

新增只读 `M2TrajectoryReader`：
1. 验证manifest结构、状态、采样数、时间单调、天体ID顺序、所有数值有限；读取 `initial_state` 并校验初始SHA256是否匹配manifest，检查首采样与初态一致。
2. 默认只接受 `COMPLETED` 归档；`COLLISION` 可作为显式研究选项运行到终止时刻；`STOPPED/FAILED` 默认拒绝，不得自动外推。
3. 轨道位置在输出点之间使用**分段三次Hermite插值**（使用 M2 位置与速度，时间采用秒），在节点上复现原值与一阶速度；不得使用简单双线性位置插值来伪造高速近距离轨道。
4. 插值不是重新进行N体积分，在近距离遭遇时可能严重失真。增加可配置的最大采样间隔与相对位置误差诊断；超出约束时要求重新以更高频率输出 M2 数据，不得声称高精度。
5. 时间仅在轨道结果定义域内有效；碰撞之后不计算辐射与温度。
6. 大规模JSONL应使用流式窗口读取，不要一次性加载数百GB。

初期可采用小样本内存 reader 完成正确性测试，再实现窗口流式 reader。所有输出标记轨道插值算法与版本。

## 4. 推荐配置格式

```yaml
schema_version: "0.3.0"
input:
  initial_state: "runs/sample/candidates/000000.initial.json"
  m2_archive: "runs/m2-example"
  allow_collision_terminated: false
  max_orbit_sample_interval_s: 86400.0
planet:
  id: "planet-1"
  rotation:
    axis_inertial_unit: [0.0, 0.0, 1.0]
    prime_meridian_inertial_unit: [1.0, 0.0, 0.0]
    rotation_epoch_s: 0.0
    initial_phase_rad: 0.0
    rotation_rate_rad_s: 7.2921159e-5
radiation:
  model: isotropic_bolometric_v1
  occultation: disabled
thermal:
  mode: both
  initial_temperature_k: 288.0
  bond_albedo: 0.3
  effective_emissivity: 1.0
  areal_heat_capacity_j_m2_k: 4.0e7
  internal_heat_flux_w_m2: 0.0
  c1:
    latitude_bands: 36
    diffusivity_w_m2_k: 0.6
  integrator:
    rtol: 1.0e-7
    atol_k: 1.0e-5
    max_step_s: 3600.0
output:
  sample_interval_s: 86400.0
  max_samples: 10000
```

示例数值仅为原型参数，不代表地球精准气候标定。所有字段采用严格Pydantic模型、`extra=forbid`、有限值约束，YAML安全加载，配置SHA-256基于规范化JSON。为避免超量计算，`max_step_s`需要在性能基准后优化。

## 5. 模块及接口（参考Python + 未来C++）

建议：
```text
src/threebody/physics/
  stellar/{models.py,radiation.py}
  rotation/{models.py,geometry.py}
  thermal/{models.py,c0.py,c1.py,diagnostics.py}
src/threebody/experiments/
  orbital_reader.py
  thermal.py
```
逻辑契约（语言中立）：
```text
CelestialEphemeris.sample(t_s) -> {id, position_m[3], velocity_mps[3]}[4]
StellarFlux.evaluate(ephemeris, luminosity_w) -> flux fields
PlanetRotation.normal(lat_rad, lon_rad, t_s) -> inertial unit vector
SurfaceInsolation.evaluate(t_s, lat_rad, lon_rad) -> W/m²
C0ThermalSolver.advance(t0_s, t1_s, forcing) -> temperature_K and energy diagnostics
C1ThermalSolver.advance(t0_s, t1_s, zonal_forcing) -> temperature_K[N] and transport diagnostics
```
未来C++核心以版本化二进制数组协议/稳定C ABI共享同一初态与强迫数据；Python保留参考实现与回归基准。M3阶段不必实现C++，但不允许Python动态对象反向成为未来科学协议的唯一规范。

## 6. CLI与产物

新增：
`threebody world thermal --config configs/m3_thermal.yaml --out-dir runs/m3-example`
保留原有`config validate`、`world generate`、`world simulate`行为。
新增归档：
```text
runs/m3-example/
  resolved_thermal_config.json
  thermal_manifest.json
  stellar_flux.jsonl
  c0_temperature.jsonl
  c1_latitude_temperature.jsonl
  energy_budget.json
```
manifest至少包含：M3模型/常数版本、SHA256、输入M2归档SHA/引用、起止模拟时间、积分容限、轨道插值与科学局限、status、采样数与错误信息。采用临时目录+原子改名模式；没有完成不能标为COMPLETED。

## 7. 里程碑和验收

### M3-01：数据边界与输入加载
- 严格配置Schema、读取M2产物、时间插值、输入审计。
- 测试：错误天体ID、无光度、负光度、非有限值、错误SHA、时间乱序、超出范围、COLLISION/STOPPED语义、近距离插值告警。
- 验收：在不改变M2的情况下读取轨道，插值在节点复现位置和速度。

### M3-02：恒星与自转、局地受光
- 三恒星通量叠加、逆平方定律、单位法向、昼夜、极昼/极夜、转轴方向。
- 测试：单恒星退化基准（测试夹具仍保留四个天体，可以把其他星光度设置为零的**测试专用辐射夹具**，不得误用于正式世界）、两倍距离通量变1/4、三个通量加和、正午/午夜、不同转速与赤纬。
- 验收：固定星场与旋转下局地日照满足几何规律；全球平均为总辐照度/4。

### M3-03：C0全球热模型
- ODE积分、积分能量预算、强迫时间插值。
- 测试：平衡温度、温度阶跃热惯性、无辐射冷却、步长收敛、热通量守恒。
- 验收：误差容差由固定基准用例确定，不能只测试“能跑”。

### M3-04：C1纬向热模型
- 等面积纬带、经度积分日照、面积守恒扩散与极点零通量。
- 测试：极区光照、全球平均日照、关闭D时列独立、纯扩散能量守恒、C0/C1辐射加权一致性、分辨率收敛。
- 验收：稳态和动态两组基准；有明确能量残差。

### M3-05：CLI、科学报告和持续集成
- 完整归档、失败不可伪装成功、旧命令与测试回归。
- 在现有GitHub Actions Python 3.11/3.12矩阵运行M3测试，新增需要的`scipy`依赖和合理上界约束。
- `pytest -q`、`ruff check .`、`python -m compileall -q src tests`。
- 形成`docs/development/M3_REPORT.md`，记录真实运行结果、基准误差、性能与限制。

## 8. 特别风险与决策

- C0/C1没有天气、降雨或风暴的预测能力；不能以这种名称宣传。
- `effective_emissivity`仅是灰体近似参数，不是经过辐射传输验证的温室效应模型。
- M2现有碰撞终止仍只到内部步级精度；M3只能演进到实际输出的终止时间。
- M2轨道采样每天一次仅是输出分辨率；极近距离遭遇或快速轨道变化时，需要更高频轨道输出和插值误差评估。
- 三恒星遮挡/日食未来应按角半径和掩食几何实现，M3不应默认有该能力。
- C1经度平均不能反映实际沿经度变化的昼夜温差，局地日照接口仅为诊断与后续扩展。

## 9. Codex实施指令

在最新main上创建`feature/p0-m3-radiation-thermal`，阅读`AGENTS.md`和本规格。
按M3-01→M3-05实施；每步先增加物理基准测试再实现。
不要修改M2计算核心；必要改动采用小且可回归的兼容补丁。
特别关注C0/C1能量守恒、M2轨道轨迹插值误差、不同模型计算时间统一，以及未来C++接口独立。
最后记录实际执行的测试、基准误差、性能和已知科学限制。
提交Draft Pull Request，**不要自动合并**，等待ChatGPT复审。
