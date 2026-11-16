# 08 — 直接交给 Codex 的启动提示词

将此文档或下文直接作为Codex任务，确保整个交付目录位于其工作仓库。

---

你现在负责实施《三体星》游戏的 P0-M0/M1 科学模拟基础工程。请先完整阅读本仓库 `AGENTS.md`、`README.md`、`docs/01_PRODUCT_AND_SCOPE.md` 至 `docs/07_IMPLEMENTATION_PLAN.md`、示例YAML和JSON Schema。

任务是**写可执行、经过测试的工程代码**，而不是只提出建议：

1. 用Python 3.11+搭建`src/threebody`项目、`pyproject.toml`、pytest、ruff和CLI入口`threebody`。
2. 实现标准SI四体初态数据模型，恰好三颗恒星、一颗行星；保存配置版本、生成器版本、seed、candidate index和坐标系。
3. 实现安全YAML配置解析、strict/research/raw预置、显式覆盖、完整解析配置与稳定SHA256；未知规则/字段应拒绝。
4. 实现**始终启用的S0数据完整性校验**与**可配置S1规则引擎**。首批五条规则：正质量、正半径、密度范围、初始表面间隙、质心归一化。规则返回PASS/FAIL/SKIP/ERROR，候选返回ACCEPTED/REJECTED/INVALID。禁用规则必须SKIP，运行错误不能算PASS。
5. 实现`cartesian`初态生成器与最小真实近似的`hierarchical_orbit`生成器。使用独立候选随机数子流以保证同seed/index/config可复现；初始条件生成不等于稳定轨道证明。
6. 实现三个CLI命令：`threebody config validate`、`threebody config resolve`、`threebody world generate`。批量输出候选初态、规则报告、汇总和manifest。
7. 实现 `docs/06_TEST_AND_ACCEPTANCE.md` 对应测试，执行pytest、ruff、一次10候选CLI烟测，并报告实际结果。
8. 不要实现M2四体积分/REBOUND运行、C0/C1、前端、数据库、多人；必要接口留文档或Python Protocol。不能创建虚假物理结果。

若发现规范不一致，以`AGENTS.md`、数据契约和规则规范为准，做最小合理假设并记入`docs/DECISIONS.md`。先列简短实施计划，然后直接实施，直到测试或资源限制为止。不要因小的非关键疑问停止工作。最终报告完成内容、验证结果、已知限制与M2准备事项。
