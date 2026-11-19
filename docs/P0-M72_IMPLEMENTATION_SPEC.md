# P0-M7.2 动态环境状态与地表观测修正规格

日期：2026-11-18。基线：M7.1 已合并的 `main`。本规格授权 M7.2-a 数据契约、
时间耦合接口、三储热最小原型、检查点和标准基准，以及不改变科学归档的地表显示修正。

## 1. 模型边界与分层

M7.2-a 是**单区域、零维、三储热动态参考模型**。它不是天气、海洋环流、板块、
大气化学或逐线辐射输运。必须区分：

1. **权威输入**：哈希验证的 M5A 轨道采样、初态质量/半径/光度、自转元数据。
2. **物理参考计算**：本规格给出的能量守恒常微分方程、SI 热容量和能量通量。
3. **参数化近似**：均匀单区域、灰体发射、线性储层换热、轨道采样间线性辐照强迫。
4. **纯视觉效果**：地形垂直夸张、HDR 曝光、程序材质、装饰背景星；不得进入科学状态。

M3/C0/C1 `0.3.0` 保留为独立科学基准。M7.2 不覆盖其配置、manifest、JSONL、
检查点或 Web API；对照报告必须同时标出模型 ID 和来源哈希。

## 2. 版本与目录

- 模型 ID：`m72_three_reservoir_v1`。
- 状态/配置/manifest schema：`0.8.0`。
- 建议派生目录：`dynamic_environments/<run_id>/`。
- 建议检查点目录：`dynamic_environment_checkpoints/<run_id>/`。
- 旧 `environments/<run_id>/` 继续只表示 M3/C0/C1，不得复用同名文件或修改原 manifest。

## 3. 权威环境状态

每个 `DynamicEnvironmentState` 对应唯一物理时刻 `t_s`，内部全部为 SI：

| 储层 | 温度 | 总热容量 | 物质库存 |
| --- | --- | --- | --- |
| 地表有效层 | `surface_temperature_k` | `surface_heat_capacity_j_k` | `surface_mass_kg` |
| 浅层岩石 | `shallow_rock_temperature_k` | `shallow_rock_heat_capacity_j_k` | `shallow_rock_mass_kg` |
| 海洋混合层 | `ocean_mixed_layer_temperature_k` 或真空值 | `ocean_heat_capacity_j_k` | `ocean_mixed_layer_mass_kg` |

状态同时记录瞬时通量，均为全行星面积平均 `W/m²`：

- `absorbed_stellar_w_m2`：M5A 三星强迫经显式 Bond 反照率和 `/4` 几何平均。
- `emitted_thermal_w_m2 = emissivity * sigma * T_surface^4`。
- `internal_heat_w_m2`：显式参数，不从地形或核心颜色推断。
- `surface_to_rock_w_m2 = k_sr * (T_surface - T_rock)`，正值表示地表向岩石。
- `surface_to_ocean_w_m2 = k_so * (T_surface - T_ocean)`，正值表示地表向海洋。
- `net_external_w_m2 = absorbed + internal - emitted`。

总能量定义为相对零温参考的显热：

`E = C_surface*T_surface + C_rock*T_rock + C_ocean*T_ocean`。

模型不包含相变潜热；当海洋库存为零时海洋温度、容量和换热必须为 `null/0`，不能生成
虚假水温。库存和热容量在 M7.2-a 内恒定，未来物质交换必须升级模型版本。

## 4. 三储热方程

令行星表面积 `A=4*pi*R^2`，三个总热容量为 `C_s/C_r/C_o`，则：

```text
C_s dT_s/dt = A [F_abs + F_internal - epsilon*sigma*T_s^4
                  - k_sr(T_s-T_r) - k_so(T_s-T_o)]
C_r dT_r/dt = A [k_sr(T_s-T_r)]
C_o dT_o/dt = A [k_so(T_s-T_o)]
```

无海洋时删除第三式并令 `k_so=0`。内部交换项在总能量预算中严格相消：

`dE/dt = A*(F_abs + F_internal - F_emit)`。

所有温度、热容量、质量、面积和通量必须有限；温度/容量/库存不得为负。模型检测到非有限、
负温度、求解失败或能量残差超限时必须失败，不能钳位后静默通过。

## 5. M5A 时间耦合接口

实现只读 `EnvironmentForcing` 协议：

```text
coverage_s -> (t_start_s, t_final_s)
source_sha256 -> 64位十六进制
sample(t_s) -> absorbed_stellar_w_m2 + source bracket/provenance
```

标准基准提供恒定强迫和辐射阶跃强迫。M5A 适配器必须：

1. 使用 `SegmentedTrajectoryReader` 验证完整已提交链，只接受 `COMPLETED`；碰撞后不外推。
2. 验证 `initial_state.json` 哈希、规范四天体、SI 和恒星光度。
3. 在每个真实 M5A 输出时刻计算三颗恒星 `sum L/(4*pi*r^2)`。
4. 动态求解器查询采样之间时，线性插值**辐照标量**；不声称恢复未采样近接峰值。
5. 强迫来源哈希覆盖 M5A 初态/物理/计划哈希、提交段哈希、真实时间网格、辐照序列、
   反照率、插值 ID 和模型版本。
6. 拒绝范围外查询、非递增时间、超过配置最大轨道间隔或来源在求解过程中改变。

统一物理时钟为 M5A 的 SI 秒。求解器内部步长、Web 播放速度、地球24小时或浏览器帧率均
不得成为强迫时间。

## 6. 配置与来源哈希

`DynamicEnvironmentConfig` 至少包含：

- `schema_version=0.8.0`、`model_id=m72_three_reservoir_v1`。
- 行星半径、反照率、发射率、内部热通量。
- 三储层质量与比热，地表—岩石/海洋换热系数。
- 初始三储层温度。
- `rtol/atol_k/max_step_s/method` 数值积分配置。
- `max_orbit_sample_interval_s`、输出时刻/间隔和最大样本数。

配置采用严格 Pydantic、拒绝未知字段和非有限值。`config_sha256` 使用规范 JSON。
`source_sha256` 与配置哈希分开；结果哈希覆盖两者、状态序列和运行时版本。

## 7. 检查点协议

检查点使用不可变 JSON `dynamic-environment-checkpoint-v1`，包含：

- schema/model ID、`t_s`、三个储层完整状态、输出索引。
- `config_sha256`、`forcing_source_sha256`、前一检查点 SHA-256。
- 项目/Python/NumPy/SciPy 版本、求解方法和容差。
- 当前检查点内容 SHA-256；禁止 NaN/Infinity、符号链接和覆盖已有检查点。

恢复前重新验证配置、强迫来源、哈希链、物理覆盖和状态不变量。M7.2-a 在检查点边界重新启动
`solve_ivp`，不声称序列化 RK 内部阶段；恢复一致性按预设容差比较，不宣称逐位一致。

## 8. 必须测试的科学基准

1. **恒定平衡**：初温为解析灰体平衡且储层同温时保持不变。
2. **恒定非平衡**：总能量变化等于外部净通量时间积分，内部换热严格相消。
3. **辐射阶跃**：升高强迫后地表先响应，岩石/海洋按热惯性滞后；不得瞬时跳到平衡。
4. **热惯性**：仅增大海洋混合层热容量时，固定时间窗内温升更小。
5. **稳定性**：长时间恒定强迫趋向有限平衡，无负温度和非有限输出。
6. **时间步收敛**：减半 `max_step_s` 并收紧容差，预设时刻温度差低于固定门限。
7. **恢复一致性**：一次运行与检查点分段恢复在固定容差内一致，能量预算连续。
8. **真实三星强迫**：短 M5A 归档→强迫适配器→动态状态，来源哈希和覆盖完全匹配。
9. **旧基准不变**：现有 M3/C0/C1 全部测试及归档读取继续通过。

## 9. M7.2 地表显示修正

- 全球地图默认且固定显示 **10 个高程级别**；标出当前观测经纬度，地图点击后标记同步。
- 海面由同一连续程序地形的高分辨率等面积样本求解，水只覆盖低于统一海平面的凹地；
  科学水库存决定海平面，不能用独立随机颜色或区域粗网格决定水体。
- 水相由温度与水蒸气分压共同判定：冰面升华、液面饱和及临界温压分支必须明确；不得使用
  固定沸点，也不得用包含不凝结气体的总压替代水分压。该参数化不声称解析非理想逸度。
- 地面恒星角直径由归档半径/距离计算并按镜头视场投影；过度消光时不画黑色星盘。
- 有足够大气散射且日间强迫足够亮时隐藏装饰背景星，保留明确的视觉阈值与说明。
- 地面地形可使用显式 `terrain_vertical_exaggeration` 视觉参数增强可读性；该值不得写入海拔、
  水量、重力、温度或任何科学结果，界面必须标注“视觉夸张”。
- WebGL 近裁剪面必须低于观察眼高，确保米级观察高度仍可看到近景地面；高仰角恒星锁定不得
  让地形三角形穿越相机平面并覆盖天空。

## 10. M7.2-a 完成定义

- 本规格、严格模型、恒定/阶跃/M5A 强迫、三储热求解器、检查点写入/恢复和上述基准测试。
- 地图/水体/恒星/背景星/地形显示修正及 Chromium 回归。
- `pytest -q`、`ruff check .`、`compileall`、JavaScript 语法、wheel 和真实短链路验收通过。
- 更新 README、决策记录和 `docs/development/M72_REPORT.md`，创建 Draft PR，不自动合并。
