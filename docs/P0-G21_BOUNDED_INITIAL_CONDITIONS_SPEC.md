# P0-G2.1 — 可配置初态距离与速度约束（G2后续增量）

前置：G2 PR #4 `c17965f`，已通过科学代码审查，仍需合并到main。G2.1独立PR，禁止修改或破坏v0.4.0输入/随机回放语义。目标是**可配置地减少高速直接逃离、远距离弱引力初态**，不是取消混沌世界。

## 设计原则
- 保留 `stochastic_conics/0.4.0` 的**全部旧字段、旧配置、旧随机结果**。通过单独的 `0.4.1` 生成器版本/专属配置schema或另一个严格版本化预设开启限制，不能把新字段默默放进v0.4.0 config并改变既有hash/行为。兼容M2/M3/VIS1。
- 约束基于**完整生成之后**的四体笛卡尔状态位置和速度（完成速度kick与全系统质心归一），而不是仅对periapsis分布裁剪。原因：偏心/双曲轨道在抽样true anomaly处的瞬时距离可能远大于periapsis。
- 约束全部可选；默认混沌档 `mode: unrestricted` 不启用速度/距离阈值，不删除高速飞掠或最终碰撞世界。可额外提供 `mode: bounded` 的示例配置。
- 实现应是**有上限、确定性的拒绝采样**：attempt=0,1,...，每次独立seed substream由 (seed,candidate_index,attempt,stream_id) 决定；不要把超标速度直接裁切或静默缩短轨道，否则将破坏运动分布/冲量记录；达到max_attempts后归档 `GENERATION_CONSTRAINT_EXHAUSTED` 和统计失败原因。
- 各约束可以独立开启，并有阈值、计算语义及度量输出。

## 建议配置（新版本，数值仅用于示例）
```yaml
generator:
  type: stochastic_conics
  version: "0.4.1"
  # 以下stars, planet, geometry与G2 v0.4.0一致
  initial_constraints:
    mode: bounded  # unrestricted | bounded
    max_attempts: 256
    planet_star_distance_m:
      min: null                  # 若设置 >0，强制行星距每颗恒星≥min
      max_nearest: 1.5e12        # 至少一颗恒星距行星≤此值（~10 AU）
      max_each: null             # 可选：到每一颗恒星均≤上限
    stellar_pair_max_distance_m: null  # 可选：三对星均满足
    planet_speed:
      absolute_max_mps: 80000.0  # 行星相对恒星系统COM的速度上限；非质心坐标绝对速度
      escape_mode: combined_potential  # none | selected_host | combined_potential
      escape_speed_fraction_max: 0.90
```

### 距离语义
`d_i=|r_planet-r_star_i|`，`d_nearest=min_i d_i`；有必要区分“最近恒星最大距离”与“三颗恒星各自最大距离”；`stellar_pair_max_distance_m`使用三对恒星的最大中心距。所有距离取**t0真实世界笛卡尔状态**，SI米，不用以坐标原点为中心的绝对位置长度替代。

### 速度/逃逸语义
`v_starCOM=Σ(m_i v_i)/Σm_i`；`v_rel=|v_planet-v_starCOM|`，这是`absolute_max_mps`检查的量，不是任意惯性系的行星绝对速度。

`selected_host`:
- 若host是某颗恒星，以`|v_planet-v_host|`对比`sqrt(2G*(m_host+m_planet)/d_host)`（二体近似）。
- 若host是`barycenter`，以星系COM相对速度和距离比较`sqrt(2G*(Σm_star+m_planet)/d_COM)`（集中质量近似）；标明此定义是有效初态指标，不是严格引力势。
`combined_potential`：
- `v_escape,proxy=sqrt(2G*Σ_i(m_i/d_i))`，以行星相对**恒星COM**的速度与之比值比较。此处三颗恒星初始速度不为零且引力势随时间变化，所以它仅是冻结恒星位置瞬时势阱的**速度标尺**，并非四体永久束缚的充分条件或严格逃逸速度。
- `escape_speed_fraction_max` 必须 >0；如设≤1，只能保证所采用近似判据下当前低于阈值。不能保证它低于每颗恒星各自的逃逸速度；如用户需要强制每颗恒星二体代理速度均低于上限，可以额外实现显式`each_star`模式，说明它高度限制可行世界。

**不要把“低于某个逃逸速度”作为禁止未来行星逃逸的科学定理。** G2游戏结局仍可出现飞离或碰撞。

## 验收
1. 新版本默认`unrestricted`与旧G2受限关闭时语义明确；v0.4.0 golden sample/config hash完全不变。
2. 同seed/index/config生成一致（包括所选attempt）且顺序独立，拒绝计数与每个reason code可追溯。
3. 大偏心与双曲离远地点/渐近线远处的样本仍按**真实d_i**约束。
4. 速度上限按星系COM和按宿主星分别测试；行星速度kick后计算；保留全部物理速度参数不通过“改小数值”逃过阈值。
5. 配置矛盾时达到max_attempts明确失败且批量程序继续后续candidate；无无限循环。
6. 初始t0接触作为合法终止世界输出（优先级与距离约束明确）：研究模式可保留，受限模式可以另有`allow_initial_contact`开关；无论如何不送入IAS15。
7. 科学S0和REBOUND强制guard不可绕过；生成器不会自动“修复”不可积分的重叠体。
8. 报告原始G2与受限G2.1在同样seed/n候选下的接触率、初始逃逸代理超限率、距离分布与拒绝率，解释统计上选择偏差。

建议提交文件：`configs/g21_bounded_worlds.yaml`、新严格版本配置+校验、`generation_metadata`扩展版本、`tests/test_g21_constraints.py`、`docs/development/G21_REPORT.md`。G2.1 Draft PR独立审查，不自动合并。
