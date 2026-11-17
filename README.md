# 三体星（Three-Body Planet）P0-M0/M1/M2/M3 + G2/VIS1

这是一个可安装的 Python 3.11+ 工程，用于生成可复现的“三颗恒星 + 一颗岩石行星”
**初始状态**，执行不可关闭的 S0 数据校验与可配置的 S1 初始物理筛选，并输出机器可读
JSON 产物。

P0-M2 使用 REBOUND IAS15 从原始 SI 笛卡尔状态进行真实 Newtonian 四体引力积分，记录
轨迹、运行期事件和守恒诊断。P0-M3 只读 M2 轨迹，计算三恒星总辐射、刚性行星自转、
C0 全球灰体热平衡和 C1 纬向扩散热模型。它不证明长期轨道稳定，也不提供天气或宜居性结论；
`hierarchical_orbit` 仍只是构造初始状态的近似方法。

## 安装与验证

使用 Conda（推荐）：

```bash
conda env create -f environment.yml
conda activate threebody-py311
pytest -q
ruff check .
```

如宿主机设置了其他 Python 版本的 `PYTHONPATH`（例如 ROS），建议为环境隔离该变量：

```bash
conda env config vars set -n threebody-py311 PYTHONPATH= PYTHONNOUSERSITE=1
conda deactivate
conda activate threebody-py311
```

也可以使用标准虚拟环境或已有 Python 3.11 环境：

```bash
python -m pip install -e '.[dev,physics,viz]'
ruff check .
pytest -q
threebody --help
```

项目要求 Python 3.11 或更新版本。核心依赖 NumPy、SciPy、Pydantic v2 和 PyYAML；
M2 的 `physics` extra 安装 REBOUND 4.x。不依赖数据库、服务端或前端框架。
轨道查看器是独立的 `viz` extra；需要时使用
`python -m pip install -e '.[dev,physics,viz]'` 安装 Plotly。

## 使用

验证配置并查看其稳定 SHA-256：

```bash
threebody config validate configs/example_experiment.yaml
```

保存完整解析后的配置：

```bash
threebody config resolve configs/example_experiment.yaml --out /tmp/resolved.json
```

生成并筛选十个候选初态：

```bash
threebody world generate \
  --config configs/example_experiment.yaml \
  --count 10 \
  --out-dir runs/sample
```

输出目录结构：

```text
runs/sample/
  resolved_config.json
  manifest.json
  summary.json
  candidates/
    000000.initial.json
    000000.screening.json
    ...
```

`manifest.json` 记录完整解析配置、配置哈希、Python/NumPy/生成器/RNG/常数集版本。
`summary.json` 包含 `total`、`accepted`、`rejected`、`invalid`、逐规则统计和每个候选的
决策。所有 JSON 都拒绝 NaN 与 Infinity，并通过临时文件加原子替换写入。

## G2 全随机四体初态

G2 新增独立的 `stochastic_conics/0.4.0` 生成器；旧 `cartesian` 和
`hierarchical_orbit/0.1.0` 的算法、解析结果和配置哈希保持不变。生成一批宽分布探索初态：

```bash
threebody world generate \
  --config configs/g2_chaotic_worlds.yaml \
  --count 100 \
  --out-dir runs/g2-chaotic
```

每个成功候选除原有 `.initial.json` 和 `.screening.json` 外，还会写出
`.generation_metadata.json`，其中记录质量/密度抽样、随机宿主、三个构造圆锥、三维方向、
真近点角、速度扰动向量和随机子流协议。`summary.json` 的 `g2_generation` 汇总宿主、偏心率、
圆锥类型、瞬时距离、速度、失败原因码和初始接触计数。

生成器允许椭圆和双曲初态，不施加层级比、长期生存、稳定性或宜居性默认筛选。`e=1` 的
抛物线模式在 0.4.0 中明确拒绝；双曲真近点角只从有效开区间抽样。若球形天体在 `t=0`
接触或重叠，会额外归档 `.initial_outcome.json`，标记为未执行积分的终止结局；M2 输入 Guard
仍会拒绝把它交给 IAS15。研究预置可保留并报告这类游戏结局，不代表点质量积分对接触后状态
有效。

### G2.1 可选初态边界

`stochastic_conics/0.4.1` 增加独立、严格版本化的生成后约束。它不会改变 0.4.0 配置、
哈希或随机结果，也不会裁切速度：

```bash
threebody world generate \
  --config configs/g21_bounded_worlds.yaml \
  --count 100 \
  --out-dir runs/g21-bounded
```

`mode: bounded` 在速度 kick 和最终质心归一后检查行星到三颗恒星的真实瞬时距离、三对恒星
距离、行星相对恒星系统质心速度，以及所选宿主二体或三恒星冻结组合势的逃逸速度代理。
不满足条件时使用有上限的确定性拒绝采样；每次尝试及原因均归档，耗尽后报告
`GENERATION_CONSTRAINT_EXHAUSTED`。`mode: unrestricted` 保留首个完整混沌样本，不应用这些
偏好。逃逸速度比例只是 `t=0` 指标，不证明未来不会飞离；碰撞和不稳定演化仍由 M2 决定。

## M2 四体轨道演进

先生成初态，再选择一个候选运行模拟：

```bash
threebody world simulate \
  --initial runs/sample/candidates/000000.initial.json \
  --config configs/m2_ias15.yaml \
  --out-dir runs/sample/simulations/000000
```

模拟目录采用临时目录完成后原子提交，包含：

```text
trajectory.jsonl                    # 严格递增时间的流式轨迹样本
events.json                         # 碰撞、近距离遭遇、逃逸候选
diagnostics.json                    # 守恒误差与每对天体最小已观察距离
resolved_simulation_config.json     # 完整 M2 数值配置
simulation_manifest.json            # 输入哈希、版本、状态和产物引用
```

IAS15 显式使用 SI 引力常数 `G=6.67430e-11`，不进行 `move_to_com`，不启用引力软化。
求解器输入 Guard 始终启用，独立于 S0/S1 筛选配置。物理接触使用 REBOUND swept-line
碰撞检测并默认终止，碰撞双方直接来自 REBOUND 回调的粒子索引，不从终止状态猜测；
碰撞时间是求解器内步检测精度，不声称无限精确。近接检测不改变
动力学；近接距离只是在 IAS15 内步端点观察到的最小值，不是连续轨道的严格数学极小值。
`ESCAPE_CANDIDATE` 使用边界、向外速度、近似比能和持续时间，只是启发式分类，
不等同于永久逃逸证明。

## VIS1 四体轨道查看器

对已有 M2 归档生成可离线打开的交互式 3D HTML：

```bash
threebody viz trajectory \
  --run runs/sample/simulations/000000 \
  --out runs/sample/simulations/000000/trajectory.html
```

默认以 AU 显示质心惯性系坐标，并内嵌 Plotly JavaScript。可使用 `--units m` 保留米，
`--frame-cap` 限制动画帧，`--max-input-samples` 限制读取规模，或用 `--cdn` 生成依赖网络的
较小文件。输出文件默认不覆盖；只有显式 `--overwrite` 才会替换。

查看器严格读取现有 `TrajectorySample`、`SimulationEvent` 和 `IntegrationResult`，不会修改、
补算或重新积分轨道。事件没有原生坐标，因此标记位置是依据 `event.body_ids` 在相邻归档采样
间做的线性显示估算，不是精确碰撞位置。天体点大小是屏幕符号尺寸，不代表物理半径；动画
播放速度也不代表物理时间流速。`STOPPED`/`FAILED` 若有完整采样仍可显示，但页面会醒目标为
部分轨迹。

## VIS2 多物理场联动观察器

用同一模拟时间轴联动 M2 四体轨道和 M3 辐射/C0/C1 数据：

```bash
threebody viz dashboard \
  --m2-run runs/sample/simulations/000000 \
  --m3-run runs/sample/thermal/000000 \
  --out runs/sample/dashboard.html
```

可选 `--m4-run` 加载经来源哈希验证的世界结局时间线；`--units au|m` 控制轨道单位，
`--frame-cap` 限制显示帧。HTML 默认内嵌 Plotly，可离线打开。桌面布局包含 3D 四体轨道、
三颗恒星独立及总辐照度、C0 温度和 C1 纬度热图；移动端自动堆叠。播放倍率只表示视觉速度。

权威时间范围严格取 M2/M3 重叠区间。轨道与温度的线性插值只用于显示，不是新的 N 体积分
或热求解；不在数据末端外推。碰撞归档在最后科学样本停止，逃逸候选若仍有 M2/M3 数据则
继续播放。质心/行星相对相机只是坐标显示变换。事件文字使用安全 JSON 和 DOM `textContent`，
不会作为 HTML 执行。原 `threebody viz trajectory` 命令保持不变。

## M3 辐射、自转和热模型

在 M2 完整归档可用后，复制并修改 `configs/m3_thermal.yaml` 中的输入路径，然后运行：

```bash
threebody world thermal \
  --config configs/m3_thermal.yaml \
  --out-dir runs/sample/thermal/000000
```

归档包含：

```text
stellar_flux.jsonl                 # 每颗恒星距离、方向、中心辐照度与三源总和
c0_temperature.jsonl              # C0 全球平均灰体温度和瞬时能量通量
c1_latitude_temperature.jsonl      # 等面积纬带温度、日照、发射与扩散收敛
energy_budget.json                 # C0/C1 积分能量残差和纬向输送守恒
resolved_thermal_config.json       # 完整 0.3.0 严格配置
thermal_manifest.json              # 输入哈希、版本、插值诊断、假设与状态
```

恒星中心辐照度采用 `L/(4πd²)`；局地顶层入射使用球面法向投影。C0 使用 SciPy RK45，
C1 使用 `x=sin(latitude)` 等面积有限体积扩散和 BDF 隐式积分。轨道节点之间采用位置与速度
约束的分段三次 Hermite 插值；它不是新的 N 体积分，近距离遭遇需要提高 M2 输出频率。
模型不包含日食、有限星盘、光谱输运、温室气体、潮汐/进动、海洋、风、降水或风暴。

## M4 环境暴露与世界结局

M4 只读并交叉验证初态、M2 和 M3 归档，生成多轴世界报告：

```bash
threebody world assess \
  --initial runs/sample/candidates/000000.initial.json \
  --m2-archive runs/sample/simulations/000000 \
  --m3-archive runs/sample/thermal/000000 \
  --config configs/m4_world_outcomes.yaml \
  --out-dir runs/sample/outcomes/000000
```

输出包含来源哈希、覆盖时间窗、环境事件、暴露持续时间、动力学事件时间线、独立的动力学/
环境分类和偏好排名。C0 冷热持续时间使用相邻采样间的线性阈值交点估算，采样极值不会描述为
连续极值。`ESCAPE_CANDIDATE` 仍不是永久逃逸证明；恒星碰撞不会被错误标记为行星碰撞；
碰撞后不外推轨道或温度。G2 初始接触可在没有 M2/M3 时生成明确的、不完整报告。

## M5A 长期模拟契约

M5A-01 新增严格的 `0.6.0` 长期运行契约和示例配置 `configs/m5a_long_run.yaml`。当前分支只
定义统一物理时钟、不可变分段索引、检查点/运行 manifest、三类兼容哈希以及任务状态机；
尚未提供 `simulate-long` 或 `resume` 命令，不能把这些契约描述成已经实现暂停恢复。

权威物理时间始终是 REBOUND 的绝对 SI 秒 `simulation.t`。输出目标由全局整数索引计算，
分段后不归零；墙钟配额、浏览器回放速度和行星自转周期均不是积分时间。首段拥有全局起点
样本，后续段采用左开右闭覆盖，避免恢复后重复边界样本。

### M5B 只读科学控制台原型

M5B-01/02 提供仅绑定回环地址的本地只读原型：

```bash
threebody web serve --artifact-root runs --host 127.0.0.1 --port 8765
```

浏览器访问 `http://127.0.0.1:8765` 可查看已提交 manifest 的服务端物理时间，并通过同一
后端严格校验实验 JSON 配置。原型不启动计算、不修改归档，也没有暂停/恢复/取消按钮；
播放和浏览属于显示行为，不能改变 REBOUND `t_s`。API 契约位于 `/api/v1/openapi.json`。

## 配置与筛选语义

配置合并优先级为内置预置配置，然后是实验 YAML 的显式覆盖。未知字段、未知规则 ID、
未知预置、非法参数和数值字符串都会被拒绝；YAML 使用 `safe_load`，不执行表达式或 Python。

预置模式：

- `strict`：五条 S1 规则启用且失败会拒绝候选。
- `research`：保留异常候选并报告；密度规则默认关闭。
- `raw`：关闭全部 S1，结果标记为 `scientific_validity=unverified`。

无论预置如何，S0 始终检查 schema、有限数值、SI 单位、坐标向量、规范 ID、唯一性以及
恰好三颗恒星和一颗行星。S1 包含：正质量、正半径、密度边界、初始表面间隙和质心误差。
禁用规则报告 `SKIP`；规则依赖或求值错误报告 `ERROR` 并使候选成为 `INVALID`。

## 可复现性

每个候选使用独立的 `PCG64(SeedSequence([seed, candidate_index, stream_id]))` 随机流。
因此候选生成顺序、其他候选失败以及筛选规则开关不会改变某个候选的天体参数。同一项目/
生成器/NumPy 版本、完整配置、seed 和 candidate index 可复现同一初态；不承诺跨运行时版本
逐位一致。

## 工程结构与边界

- `src/threebody/config`：安全 YAML、预置合并、严格模型、规则参数校验和配置哈希。
- `src/threebody/world`：SI 数据模型、旧生成器与 G2 随机圆锥初态生成器。
- `src/threebody/screening`：强制 S0、五条 S1 规则、决策聚合和筛选报告。
- `src/threebody/experiments`：批量候选运行、manifest 和统计汇总。
- `src/threebody/physics/nbody`：REBOUND IAS15、强制输入 Guard、诊断与事件观察器。
- `src/threebody/visualization`：M2 归档校验、显示变换和独立 Plotly 3D 查看器。
- `src/threebody/physics/stellar`：三恒星各向同性总辐射和球面/纬带入射。
- `src/threebody/physics/rotation`：固定转轴的刚性球体自转几何。
- `src/threebody/physics/thermal`：C0 全球与 C1 纬向科学参考模型及能量诊断。
- `src/threebody/integrator.py`：稳定 Protocol 门面和 M2 具体类型导出。
- `tests`：对应 `docs/06_TEST_AND_ACCEPTANCE.md` 的单元与端到端测试。

科学和配置契约详见 `docs/`，实现时的明确取舍记录在 `docs/DECISIONS.md`。
