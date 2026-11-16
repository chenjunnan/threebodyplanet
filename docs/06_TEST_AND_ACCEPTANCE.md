# 06 — 测试矩阵与M0/M1验收

## 1. 必须具备的测试

| 编号 | 场景 | 预期 |
|---|---|---|
| T01 | 合法YAML与preset | 展开为完整配置且hash稳定 |
| T02 | 未知顶层字段/规则ID | ConfigError，CLI非0退出 |
| T03 | 禁用某S1规则 | 该规则输出SKIP，不参与拒绝 |
| T04 | 负质量+strict | FAIL并REJECTED（不因数学非有限而错误标S0） |
| T05 | 负质量+raw | S0仍通过，S1全部SKIP，标unverified；不得送M2 |
| T06 | NaN/Infinity任意核心字段 | S0 INVALID，无非法JSON输出 |
| T07 | 负半径+strict | 对应S1 FAIL并拒绝 |
| T08 | 两体初始重叠+strict | `system.initial_overlap` FAIL并拒绝 |
| T09 | 重叠规则禁用 | SKIP且不因该规则拒绝 |
| T10 | 温度极高 | 不由S1单独拒绝；S2未实现 |
| T11 | 规则评估内部错误 | ERROR、候选INVALID，不静默通过 |
| T12 | 相同seed/index/config | 初态相同，报告允许非语义时间戳差异 |
| T13 | 不同筛选设置但同生成器参数 | 初态相同，筛选结果可不同 |
| T14 | 候选N失败 | 候选N+1随机参数不改变 |
| T15 | 生成器输出4体和唯一ID | S0满足，坐标长度均为3 |
| T16 | 质心归一化 | 相对误差在浮点容差范围 |
| T17 | JSON写出/读回 | 浮点与重要字段保持一致 |
| T18 | 批量count=10 | summary计数之和=10，每个候选有报告 |
| T19 | exploration.enabled=true | 明确FeatureNotImplemented，不假装模拟 |
| T20 | 配置 hash | 解析后的等价配置hash相同，物理配置变化hash改变 |

## 2. 精度与科学检查

- 行星密度公式 `rho=M/(4/3*pi*R**3)` 的标准算例。
- 初始重叠判定使用球体表面间隙，单位m。边界等于阈值应通过（约定比较容差并测试）。
- 质心位置与速度应以系统尺度归一化测量，而非绝对距离接近0这种不适用于天文尺度的断言。
- 几何生成中不产生无限速度/数值溢出；超范围参数报告ERROR或ConfigError。

## 3. 命令行验收

```bash
python -m pip install -e '.[dev]'
ruff check .
pytest -q
threebody config validate configs/example_experiment.yaml
threebody config resolve configs/example_experiment.yaml --out /tmp/threebody-resolved.json
threebody world generate --config configs/example_experiment.yaml --count 10 --out-dir /tmp/threebody-run
```

CLI所有命令必须提供`--help`，异常时非0退出、打印可读错误。生产工具可配置`--fail-fast`但默认保留全部规则报告。

## 4. 产物验收

- `resolved_config.json`、`manifest.json`、`summary.json`、每个候选的screening报告。
- 正常候选有`.initial.json`，即使S1拒绝也应保留初态供诊断。
- summary至少输出`total/accepted/rejected/invalid`与每条规则`passed/failed/skipped/errored`。
- 报告明确 `scientific_validity` 为 `screened|unverified|failed`，严禁将筛选通过等同于四体长期稳定。
- 数据中不出现 `NaN`、`Infinity`、未识别单位或未记录配置版本。

## 5. 本阶段禁止的伪验收

- 不要输出“已验证百年稳定轨道”：M1尚未做轨道积分。
- 不要输出“已经实现温度/天气”：M1没有气候模型。
- 不要以自动生成的随机数对照自身作为唯一科学验证。
- 不要写`pass`空函数后把功能勾选为完成。

## 6. 完成报告模板

Codex最终答复需提供：完成模块、创建/修改文件、实际执行的命令及结果、未实现与风险、下一步M2建议。若存在测试失败必须明确说明。
