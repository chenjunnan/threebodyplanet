# 04 — 配置解析、筛选规则与可扩展性规范

## 1. 四层语义（不可合并）

- S0：输入类型、有限数值、schema、单位、4天体与唯一ID、配置冲突。**始终执行**。
- S1：初始物理检查，按规则配置开关执行。包含正质量/半径、密度范围、初始表面间隙等。
- S2：未来的探索适宜性检查，依赖积分与气候模型。本轮**不执行、不声称通过**。
- S3：未来的运行中事件检测和响应（碰撞/逃逸）。本轮仅在文档中保留独立接口。

## 2. 配置优先级和冻结

解析顺序：内置默认 → `preset` 文件 → 实验配置的显式 `screening.initial_physics.rules` / `screening.exploration` 覆盖。只允许覆盖已有已注册规则的合法字段。解析后生成完整配置快照、SHA256；后续不访问原YAML来解释同一实验。

建议：`preset=strict|research|raw` 使用项目内受版本控制的preset。配置中未知preset、未知规则、未知属性、非法类型都直接报ConfigError。禁止脚本或动态import来定义规则。

## 3. 规则协议

```python
class ScreeningRule(Protocol):
    id: str
    version: str
    phase: Literal["initial"]
    def validate_params(self, params: dict) -> None: ...
    def evaluate(self, state: WorldInitialState, params: dict) -> RuleResult: ...
```

定义：`status = PASS | FAIL | SKIP | ERROR`；`severity = error | warning | info`；`failure_action = reject | report`。

- **推荐匹配规则**：`severity=error`可用于`reject`或`report`（当作为硬科学异常但研究模式需保留）；`warning`与`info`只能`report`，避免展示与决策不一致。
- `enabled=false` 必须输出SKIP；`SKIP`不检查物理数值。
- `enabled=true`且依赖缺失 => ERROR，不能静默PASS。
- 任何 `ERROR` 使候选 `INVALID`；S0失败亦如此。
- 所有 enabled 且 FAIL 且 `failure_action=reject` => 候选REJECTED；其他情况ACCEPTED，并报告warning和不确定性。
- 处理`fail_fast`需保持规则结果清晰：未执行项标记SKIP并注明 `reason=stopped_after_failure`，不能标PASS。
- 规则执行顺序按注册表显式顺序，稳定；未来增加规则不改变当前规则的计算结果。

## 4. M1首批规则

| id | 参数 | 计算说明 |
|---|---|---|
| `body.mass.positive` | 无 | 所有body `mass_kg > 0` |
| `body.radius.positive` | 无 | 所有body `radius_m > 0` |
| `body.density.bounds` | `min_kg_m3`,`max_kg_m3`,`kinds` | 按 `3M/(4πR³)`，仅检查指定类型；不作为完整内部结构验证 |
| `system.initial_overlap` | `minimum_surface_gap_m` | 每一对天体中心距减两半径之和，检查≥阈值 |
| `system.barycenter` | `max_relative_position_error`,`max_relative_velocity_error` | 归一化误差，对系统尺度归一，不能使用m单位的零绝对容差 |

扩展规则（`star.main_sequence.profile`、`planet.composition.consistency`、`system.velocity_policy`）先保留在路线图，**不要以空判断PASS冒充实现**。新规则加进注册表前必须有算法、参数Schema和测试。

### 初期推荐的S1默认

- `strict`: 质量、半径、密度、初始重叠、质心约束启用并可reject。
- `research`: 质量、半径、重叠开启，但`failure_action=report`，密度检查可关闭。
- `raw`: 全部S1关闭，保留S0；输出 `scientific_validity=unverified`；不能直接送给默认M2积分器。

## 5. 运行期安全边界

S1关闭质量正值规则可以让研究配置记录负质量候选，但进入未来真实积分器前**必须由单独的 `IntegratorInputGuard` 拒绝不符合默认牛顿天体求解域的输入**。不要把可配置规则代替基础安全检查。

## 6. 失败与未知处理

- 配置值`null`用于必须字段 => ConfigError。
- 规则无权修改输入状态。
- 不支持的`screening.exploration.enabled=true`应在M1报明确`FeatureNotImplemented`配置错误，而不是静默跳过。示例配置应设为false。
- 配置内 `simulation`（积分、输出间隔）本轮仅允许标记为future metadata，不可触发无实现的积分。
- 严格验证 `candidate_count >= 1`，`max_candidates >= candidate_count`，seed为非负整数且在约定范围内。

## 7. 扩展新增规则的要求

新增rule必须提交：稳定规则id、规则版本、参数校验、适用类型、`evaluate()`、正反例测试、文档说明。不能直接在生成器内部追加临时if。
