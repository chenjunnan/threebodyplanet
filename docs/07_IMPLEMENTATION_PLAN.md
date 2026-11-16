# 07 — Codex 执行顺序与任务拆分

## 阶段0：初始化（M0-01）

- 建立`pyproject.toml`与`src/threebody/`布局，Python>=3.11。
- 配置pytest和ruff、安装说明、`threebody --help`。
- 验收：安装成功、空测试可跑、导入包成功。

## 阶段1：数据与数值契约（M0-02）

- 数据模型：CelestialBody、WorldInitialState、RuleResult、ScreeningReport、ResolvedExperimentConfig。
- SI常数集、向量校验、稳定JSON序列化、SHA-256工具。
- 验收：T06、T15、T17。

## 阶段2：配置引擎（M0-03）

- 安全YAML加载；preset合并；不支持字段和规则ID拒绝；配置冻结与hash。
- 三份preset和示例配置保持一致。
- 验收：T01、T02、T19、T20。

## 阶段3：S0/S1引擎（M1-01）

- 实现S0不可禁用校验，规则注册表、规则配置、决策聚合。
- 实现五条初始规则：质量、半径、密度、重叠、质心。
- 验收：T03–T11。

## 阶段4：生成器（M1-02）

- 实现确定性随机数子流、`cartesian`、`hierarchical_orbit`最小版本。
- 采用清楚的物理近似构建质量/半径和相对速度；最终质心归一化。
- 验收：T12–T16。

## 阶段5：批量实验与CLI（M1-03）

- `config validate`、`config resolve`、`world generate`。
- 输出完整候选集合与报告，统计每条规则的通过/失败/跳过/出错次数。
- 验收：T18和全部CLI烟测。

## 阶段6：集成与文档（M1-04）

- 加端到端测试、规范示例、README使用说明、DECISIONS日志。
- 执行ruff、pytest与一次10候选生成；确认无非有限JSON。
- 收尾只汇报真实完成的内容。

## 重要约束与优先级

首要是：**契约正确、规则真正可配置、数据安全、重复性可验证**。若时间不足，优先完成`cartesian`生成器、规则引擎、CLI和测试，再补全层级轨道生成器；不得用空接口假装后者完成。允许拆成多个Codex PR提交，每个PR要求测试通过。

## M2启动前必须保留的接口信息

- 标准SI四体初态；恒星半径/质量；唯一ID和坐标系。
- `IntegratorInputGuard`的未来需求（不等于S1）。
- 事件输出格式和质量守恒/数值诊断的预留字段。
- 未来积分器应能在不改变初态生成模块的情况下接入。
