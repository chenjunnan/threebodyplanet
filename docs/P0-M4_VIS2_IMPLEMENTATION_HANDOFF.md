# P0-M4 + VIS2 进入开发阶段 — Codex执行指令

## 当前GitHub基线
- G2 PR #4: `feature/p0-g2-stochastic-worlds`, HEAD `c17965f`，审查认为符合原G2范围，CI Python3.11/3.12成功。
- 当前本设计分支: `design/p0-g21-m4-vis2-iteration`，包含新G2.1初态约束规格。
- 原M4规范: `docs/P0-M4_ENVIRONMENT_OUTCOME_SPEC.md`；VIS2规范: `docs/P0-VIS2_ORBIT_RADIATION_TEMPERATURE.md`。
- 旧M3+VIS1已在main；需先确认G2 PR #4合并，否则M4不应假定G2新初始接触的归档语义已经进入main。

## 编码启动顺序
**首先实施G2.1约束**（可在G2合并main后从main+设计规格分支开始），但**M4与VIS2可以独立并行开发**；不要只写文档。

### 工作流A：G2.1
1. 创建 `feature/p0-g21-bounded-initials`，阅读 `docs/P0-G21_BOUNDED_INITIAL_CONDITIONS_SPEC.md`。
2. 新配置版本及独立预设；检查生成后的真实planet-star距离、相对恒星系统COM速度、宿主二体代理/总势代理逃逸阈值。支持mode=unrestricted。保存每次尝试与拒绝原因，max_attempts有限。
3. 无法满足严格阈值时要明确退出，不能随机重抽无上限，也不能暗中修改速度。
4. 全部M0/M1/M2/M3/VIS1/G2回归，发布独立Draft PR。

### 工作流B：M4
1. 从确认已合并G2的**最新main**创建 `feature/p0-m4-world-outcomes`。
2. 真正实现 M4-01~05：M2/M3/G2 manifest/hash/time适配、可配置环境暴露阈值和持续时间、碰撞/逃逸/热异常事件时间线、完整世界分类、报告与CLI `threebody world assess`、测试/CI。
3. 必须区分`star-star collision`和`planet involved collision`，初始接触缺M2/M3时仍可出报告，`ESCAPE_CANDIDATE`不等于永久飞离；事件后不得凭空延伸轨道温度。
4. 结果可作为VIS2可选输入；完成后独立Draft PR，禁止自动合并。

### 工作流C：VIS2
1. 从最新main单独创建 `feature/p0-vis2-linked-dashboard`，**不要阻塞M4**。
2. 真正实现VIS2-01~05：完整M2/M3数据验证，同一时间轴同步四体轨道、三个恒星独立辐照度、总辐射、C0温度、C1纬度热图；可选M4事件时间线。
3. 特别关注M2、M3数据的time/hash关系与不同采样间隔。对于物理缺失区间不外推；C0/C1可视化插值必须标记显示用途；碰撞后停止；escape candidate后若存在轨迹继续播放。
4. 离线HTML，安全文字处理，Chrome/Edge的人工交互验收及自动化UI验证；保留VIS1命令。独立Draft PR。

## 共同CI门槛
```bash
pytest -q
ruff check .
python -m compileall -q src tests
```
必须真实运行测试，提交`docs/development/G21_REPORT.md`、`M4_REPORT.md`、`VIS2_REPORT.md`（各分支对应），展示真实样例归档、数值统计、失败场景和注意事项。

## 不可变的物理边界
- 随机采样只是生成初始条件；M2根据引力积分，不会预设轨迹。
- G2.1有限速度/距离条件是**可选世界偏好**，不是强制科学定律。
- 所有参考系和SI单位必须公开；自转日、输出采样间隔和游戏时间速度互不等同。
- 继续保留“世界毁灭与高速飞掠”的混沌档。
- 单纯生成静态3D效果、固定动画、凭外推显示碰撞后轨迹，不能算VIS2完成。

## 交付与阶段合并
先审查G2 PR #4并由维护者合并→G2.1/M4/VIS2分别Draft PR→独立科学/代码审查与CI→分别合并。禁止自动合并，且各PR不得夹带其他分支未合并的代码。
