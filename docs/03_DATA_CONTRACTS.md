# 03 — 数据模型、单位与可复现性

## 1. 权威数据模型

### CelestialBody

```json
{
  "id": "star-1",
  "kind": "STAR",
  "mass_kg": 1.98847e30,
  "radius_m": 6.957e8,
  "position_m": [0.0, 0.0, 0.0],
  "velocity_mps": [0.0, 0.0, 0.0],
  "physical_profile_id": "main_sequence_v1",
  "properties": {"luminosity_w": 3.828e26, "effective_temperature_k": 5772.0}
}
```

行星 `kind=PLANET` 的 `properties` 可包含 `rotation_axis_unit`、`rotation_rate_rad_s`、`bond_albedo`、`surface_heat_capacity_j_m2_k`、`composition_profile_id`。参数在当前版本不用于N体积分。字段扩展必须在版本中登记，不把未实现的科学推论藏到properties里。

M7.1 的 `stochastic_conics/0.4.3` 在行星 `properties` 中增加
`atmosphere_hypothesis`。它必须包含 `model=retention_weighted_prior_v1`、
`is_observation=false`、发生率抽样、旋转修正逃逸速度/Jeans 代理、质量和五种气体摩尔分数。
该对象是带来源的随机先验，不得作为 S0/S1 大气事实或 M2 引力输入。0.4.2 及更旧世界不得
被读取器自动补写该对象。M7.2 环境地貌修正后的场景输出使用
`planet-exploration-v5`；其中 `terrain_model=environment_morphology_proxy_v1` 必须记录固定参考
温度/压力、自转参数、水质量分数、侵蚀/断裂指数及全部平原、山脊和断裂参数。M5A、M3、M4
及旧 `planet-exploration-v2/v3/v4` schema 保持各自语义，
不随 Python 包版本 `0.8.0` 静默改变。

M7.2-a 的权威动态环境状态使用 `schema_version=0.8.0`、
`model_id=m72_three_reservoir_v1`，记录地表、浅层岩石和海洋混合层温度、质量、总热容量、
瞬时能量通量及总显热。它写入独立派生域，不覆盖 M3/C0/C1。结果、配置、M5A 强迫来源和
检查点各自有 SHA-256；恢复必须重新验证配置和来源一致性。

### WorldInitialState

```json
{
  "schema_version": "0.1.0",
  "world_id": "candidate-000000",
  "seed": 20261116,
  "candidate_index": 0,
  "generator_id": "hierarchical_orbit",
  "generator_version": "0.1.0",
  "config_sha256": "<64 lowercase hex digits>",
  "units": "SI",
  "coordinate_frame": "BARYCENTRIC_INERTIAL",
  "reference_epoch_s": 0.0,
  "bodies": []
}
```

`bodies` 必须恰好包含三颗STAR和一颗PLANET（此M1版本），四个ID不同，固定规范顺序 `star-1`、`star-2`、`star-3`、`planet-1`。位置/速度三分量均有限；质量、半径的正负性属于S1，但默认N体兼容性策略必须在M2调用前单独执行。S0 对非有限浮点、缺字段、类型错误严格拒绝。

## 2. SI与坐标约定

- 全部内部数值采用SI；恒星质量生成器可采样太阳质量倍数，但写入 `mass_kg` 前转换。
- 位置 `position_m` 为宇宙质心惯性系；速度 `velocity_mps` 同一参考系。
- `G = 6.67430e-11 m^3 kg^-1 s^-2` 作为初版常数，记录常数集版本。
- 固定参考历元可设为模拟t=0；不得混用UTC时间戳与动力学时间。
- 有限值验证必须递归应用到所有核心数值；标准JSON禁止NaN/Infinity。

## 3. 决策与规则报告

```json
{
  "candidate_index": 0,
  "decision": "REJECTED",
  "results": [
    {
      "rule_id": "system.initial_overlap",
      "rule_version": "0.1.0",
      "status": "FAIL",
      "severity": "error",
      "failure_action": "reject",
      "observed": {"surface_gap_m": -1.0},
      "thresholds": {"minimum_surface_gap_m": 0.0},
      "message": "body surfaces overlap"
    }
  ]
}
```

筛选报告还包含 `initial_state_diagnostics`，集中记录初态的有符号总质量、质心位置和
速度、最小表面间隙以及最大初始相对速度。它们只是初态诊断，不是轨道稳定性、束缚性或
宜居性结论。

`PASS|FAIL|SKIP|ERROR` 是规则结果；`ACCEPTED|REJECTED|INVALID` 是候选决策。**`severity` 仅供展示，决策由 `failure_action` + 结果决定**；建议禁止矛盾组合（见规则规范）。报告不允许缺失规则版本或无由来的沉默跳过。

## 4. 文件产物格式

```text
runs/<experiment-id>/
  resolved_config.json
  manifest.json
  summary.json
  candidates/
    000000.initial.json
    000000.screening.json
    000001.initial.json
    000001.screening.json
```

S0失败到无法构造初态时允许省略 `.initial.json`，但必须存在失败报告，并保留不会泄露秘密的原始候选参数诊断。`manifest.json`包含Python/NumPy版本、项目版本、常数集、随机算法、配置哈希和生成时刻（生成时刻不参与配置哈希）。

## 5. 复现契约

- 推荐 `numpy.random.Generator(PCG64(SeedSequence([seed, candidate_index, stream_id])))`；各候选生成独立随机流，内部不同用途采用独立stream_id。
- `same seed + same candidate_index + same resolved config + same generator version + same RNG library/runtime` => 同初态。跨版本、跨设备逐位完全一致**不承诺**。
- 哈希：将解析完成的配置转换为稳定字段顺序、稳定数字类型的canonical JSON，UTF-8编码后SHA-256。`config_sha256`应排除输出路径、日志级别和运行时间等非语义字段；文档中列明具体排除项。
- 浮点严格保留精度；不要在计算中途round，仅可以在显示层格式化。

## 6. 数据不变量

- 序列化可读取回来且关键字段数值一致。
- 每个候选有稳定、唯一的 `world_id`，与随机seed + index映射规则公开。
- 生成器输出质心位置与质心速度接近0，提供相对误差诊断；不是通过“把第四个天体放在原点”实现。
- `properties` 不允许嵌入任意对象实例或不受控表达式。

## 7. M3 辐射与热归档（0.3.0）

M3 不修改上述 0.1.0 初态或 M2 的 0.2.0 契约。独立配置使用 SI 字段后缀，输出
`thermal_manifest.json`、`stellar_flux.jsonl`、`c0_temperature.jsonl`、
`c1_latitude_temperature.jsonl` 和 `energy_budget.json`。manifest 记录初态与 M2 manifest
哈希、Hermite 插值版本、旋转相位参考、SciPy/NumPy/项目版本和模型假设。温度为 K，辐射与
热通量为 W/m²，面热容为 J/m²/K，积分能量为 J/m²；所有结构化数值必须有限。
