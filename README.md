# 三体星（Three-Body Planet）P0-M0/M1/M2

这是一个可安装的 Python 3.11+ 工程，用于生成可复现的“三颗恒星 + 一颗岩石行星”
**初始状态**，执行不可关闭的 S0 数据校验与可配置的 S1 初始物理筛选，并输出机器可读
JSON 产物。

P0-M2 使用 REBOUND IAS15 从原始 SI 笛卡尔状态进行真实 Newtonian 四体引力积分，记录
轨迹、运行期事件和守恒诊断。它不证明长期轨道稳定，也不计算气候或宜居性；
`hierarchical_orbit` 仍只是构造初始状态的近似方法。

## 安装与验证

```bash
python -m pip install -e '.[dev,physics]'
ruff check .
pytest -q
threebody --help
```

项目要求 Python 3.11 或更新版本。M0/M1 核心依赖 NumPy、Pydantic v2 和 PyYAML；
M2 的 `physics` extra 安装 REBOUND 4.x。不依赖数据库、服务端或前端框架。

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
diagnostics.json                    # 守恒误差与每对天体最小距离
resolved_simulation_config.json     # 完整 M2 数值配置
simulation_manifest.json            # 输入哈希、版本、状态和产物引用
```

IAS15 显式使用 SI 引力常数 `G=6.67430e-11`，不进行 `move_to_com`，不启用引力软化。
求解器输入 Guard 始终启用，独立于 S0/S1 筛选配置。物理接触使用 REBOUND swept-line
碰撞检测并默认终止；碰撞时间是求解器内步检测精度，不声称无限精确。近接检测不改变
动力学；`ESCAPE_CANDIDATE` 使用边界、向外速度、近似比能和持续时间，只是启发式分类，
不等同于永久逃逸证明。

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
- `src/threebody/world`：SI 数据模型、`cartesian` 与 `hierarchical_orbit` 初态生成器。
- `src/threebody/screening`：强制 S0、五条 S1 规则、决策聚合和筛选报告。
- `src/threebody/experiments`：批量候选运行、manifest 和统计汇总。
- `src/threebody/physics/nbody`：REBOUND IAS15、强制输入 Guard、诊断与事件观察器。
- `src/threebody/integrator.py`：稳定 Protocol 门面和 M2 具体类型导出。
- `tests`：对应 `docs/06_TEST_AND_ACCEPTANCE.md` 的单元与端到端测试。

科学和配置契约详见 `docs/`，实现时的明确取舍记录在 `docs/DECISIONS.md`。
