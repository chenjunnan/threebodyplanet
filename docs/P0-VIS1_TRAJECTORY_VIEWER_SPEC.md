# P0-VIS1：M2四体轨道交互可视化器 — 实施规格

状态：设计完成，待VS Code Codex实现
基线：main（已经包含M2 PR #1）
开发分支建议：feature/p0-vis1-trajectory-viewer

## 1. 目标

提供一个**独立于M2积分和M3热力学求解**的只读诊断工具：输入一个M2归档目录（包含 trajectory.jsonl、events.json、simulation_manifest.json），输出可交互、可离线查看的3D HTML。用于快速观察四体轨道、时间演进及事件位置，不以图像观感替代科学验证。

技术选型：Python 3.11+ + Plotly（单独可选依赖extra `viz`；不增加基础包的必需依赖）。复用Pydantic M2数据模型，不引入服务器、数据库、Babylon.js或重型GUI。

## 2. 真实M2数据契约（基于main源码）

- `trajectory.jsonl`：**逐行JSON对象**；每行符合 `threebody.physics.nbody.models.TrajectorySample`：
  `{"t_s": number, "bodies": [{"id": "star-1", "position_m":[x,y,z], "velocity_mps":[vx,vy,vz]}, ..., {"id":"planet-1", ...}], "diagnostics": {...}}`。
  `bodies`固定顺序 `star-1,star-2,star-3,planet-1`。实际输出包含完整诊断字段，使用已有模型校验。
- `events.json`：`SimulationEvent`数组，事件种类 `COLLISION`、`CLOSE_ENCOUNTER`、`ESCAPE_CANDIDATE`；事件有`t_s`、`body_ids`、`observed`、`message`、`detection_method`，**没有空间坐标**，故必须根据轨迹插值估算事件显示位置；此估算不是精准碰撞位置。
- `simulation_manifest.json`：`IntegrationResult`，包括`status`（`COMPLETED | COLLISION | STOPPED | FAILED`）、`t_start_s`、`t_final_s`、`sample_count`、`event_count`、`artifact_refs`及诊断摘要。
- M2位置单位为**米**，时间单位为**秒**；在显示时可以把坐标除以 `149597870700.0` 转成AU，把时间转成天/年；**必须始终标明显示单位和参考坐标系**。
- `artifact_refs.initial_source` 是生成时的绝对路径，拷贝归档后可能无效。VIS1 **不得依赖这个文件存在**；如有用户明确提供初态，可额外比对hash，但不阻止无初态文件的可视化。
- 不能假定M2归档包含天体半径（轨迹BodyTrajectoryState没有半径）。视觉标记大小是屏幕符号尺寸，不代表物理大小。

## 3. CLI 与目录

新命令：
```bash
python -m pip install -e '.[dev,physics,viz]'
threebody viz trajectory --run runs/m2-example --out runs/m2-example/trajectory.html
```

新增包：
```text
src/threebody/visualization/
  __init__.py
  reader.py       # schema validation + archive metadata
  transforms.py   # 坐标与显示单位转换、事件位置插值
  plotly_viewer.py  # 静态轨迹、动画帧、控件、HTML
tests/
  test_vis_reader.py
  test_vis_transforms.py
  test_vis_plotly.py
docs/development/VIS1_REPORT.md
```
在`pyproject.toml`增加`viz=["plotly>=5.18"]`作为可选依赖；`threebody viz trajectory`应对未安装Plotly给出明确安装说明，不影响既有`world`或`config`命令。CLI使用 `--run` 指向归档目录、`--out` 输出.html、`--frame-cap` 限制播放帧数、`--units au|m` 和 `--center barycentric|planet`（可以先实现barycentric，planet作为后续可选里程碑，不得提供失效选项）。默认不覆盖已存在输出文件；提供显式`--overwrite`（可选）。

## 4. 验证与拒绝策略

reader必须：
1. 逐行读取JSONL，正确处理空行策略（建议拒绝空行）、损坏JSON、非法Unicode、非有限数值、缺少字段。
2. 使用现有 `TrajectorySample.model_validate(..., strict=True)` 等模型校验，核对每行恰好四个天体且ID/顺序恒定。
3. 验证时间严格递增（允许合法的碰撞提前终止），首尾时间、样本数、事件数与manifest一致；容许浮点终点误差只能使用明示的有限容差，不可默默忽略差异。
4. 事件时间必须在归档区间；如果超出，要明确警告或拒绝显示该事件，不能外推到不存在的未来。
5. `COMPLETED`/ `COLLISION` 为正常可视化；`STOPPED`/ `FAILED` 仍可将已有完整样本作为**部分轨迹**显示，但必须醒目标注非完成状态，不得误称“已完成轨道”。
6. 必须禁止HTML脚本注入：天体ID是固定枚举，事件message和归档文本要由Plotly进行安全文本处理或做转义，不允许直接拼接入未转义HTML/JS。
7. 防止无限量内存占用。首版允许设置`max_input_samples`和`frame_cap`；超过绝对输入规模上限需给出清晰错误，不能无提示地读入数百万样本。大规模流式/二进制是后续VIS2任务。

## 5. 显示和交互

- 轨迹：四条3D折线，分别标记星1、星2、星3和行星；支持图例显示隐藏。
- 时间：播放/暂停、离散时间滑块，显示当前t相对历元的秒/天；不能把帧播放速度等同物理积分速率。
- 当前位置：四个动态标记（可选有方向性速度箭头；首版无需）；静态轨迹绘制一次，每帧只更新4个标记，避免`O(n²)`的大帧包。
- 保证三个坐标轴等比例，否则轨迹形状会畸变；Plotly scene.aspectmode='data' 或等效处理。
- 默认`AU`显示，明确`1 AU = 149597870700 m`。
- 事件：对非精确采样时刻`t_event`，使用相邻两采样点对相关天体**分段线性插值**来标注**仅供显示的位置估算**。如果使用Hermite则须复用M3插值实现/有独立测试，不能把可视化事件插值描述为物理精确接触位置。
  - 双体事件的展示位置可取两个相关天体插值位置的中点；逃逸候选标注其对应天体的位置。
  - `t_event`不在轨迹范围内时明确拒绝/跳过该事件；不外推。
  - 不以距离最近的一对天体推断碰撞主体；严格使用`event.body_ids`。
- 时间标记和事件Tooltip同时标注事件类型、时间、天体、检测方法和“采样插值估计”说明。
- 天体视觉符号可用不同颜色与点大小，但**明确声明标记半径不是物理半径**。
- 默认生成脱机自包含HTML（Plotly JS通过内嵌），允许选项`--cdn`生成较小但依赖互联网的HTML。
- 大量轨迹时，静态线可以均匀抽稀且保留首尾、事件邻近采样、关键几何转折；动画帧限制不应无提示删掉事件或尾帧。用户须知道抽稀或仅展示部分采样。

## 6. 接口和性能设计

建议：
```python
@dataclass(frozen=True)
class TrajectoryArchive:
    manifest: IntegrationResult
    samples: tuple[TrajectorySample, ...]
    events: tuple[SimulationEvent, ...]

def load_m2_archive(run_dir: Path, *, max_samples: int) -> TrajectoryArchive: ...

def build_trajectory_figure(archive: TrajectoryArchive, *,
    units: Literal['au','m']='au', frame_cap: int=1000) -> plotly.graph_objects.Figure: ...

def export_trajectory_html(...): ...
```

当前建议将 Plotly 代码与数据reader严格分离。读取的事件和采样可以做C0/C1温度曲线的未来输入，但不增加对M3的依赖；未来 P1 Web3D 可替换渲染器，而复用底层归档契约。

## 7. VIS1里程碑和验收测试

VIS1-01 — 读取与校验：构建真实M2模型的测试夹具，检查文件存在性、JSONL错误、时间顺序、四体数量/ID、manifest样本数/终点/状态，坏输入明确报错。

VIS1-02 — 3D绘图：四条轨迹、初始+终态位置、AU转换、轴比例、不按真实半径画点、HTML可离线打开；单样本和零事件也能安全显示（只有一个时间点时无需播放）。

VIS1-03 — 动画与事件：滑块、播放暂停、帧上限、离散采样、事件时间插值、严格使用事件body_ids、碰撞停在终止时刻、STOPPED/FAILED部分数据醒目提示。核对frames更新的trace索引仅指向动态点而不重复全量轨迹。

VIS1-04 — CLI、文档与CI：新增可选依赖、README示例、`docs/development/VIS1_REPORT.md`、CI中的Plotly可选依赖测试。旧M0/M1/M2命令与回归测试不能破坏。

必须执行：
```bash
pytest -q
ruff check .
python -m compileall -q src tests
threebody viz trajectory --run runs/m2-example --out runs/m2-example/trajectory.html
```
最后一条依赖真实归档；若不可用，需通过生成一个小型真实M2轨迹实现端到端验收。HTML验收还必须检查帧数量、trace数量、嵌入脚本资源和文件大小；仅“保存成功”不代表动画正确。

## 8. 实施交付与PR

Codex以本设计分支`design/p0-vis1-trajectory-viewer`为起点创建`feature/p0-vis1-trajectory-viewer`，遵守根目录AGENTS.md的科学安全约束，但该文件中的历史M0/M1或M3范围不限制独立VIS1；**当前工作的权威任务规格是本文档**。

建议GitHub Draft PR → main，独立于M3分支；不要合并其他里程碑尚未完成的代码。完成后请报告PR URL、Head SHA、真实M2归档演示命令、完整测试结果、HTML大小和事件展示限制。等待复审，不要自动合并。
