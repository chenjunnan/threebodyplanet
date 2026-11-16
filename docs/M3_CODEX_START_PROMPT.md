# P0-M3 Codex 启动指令（VS Code 插件）

仓库：`chenjunnan/threebodyplanet`

1. 从 GitHub 拉取最新远程变更。**注意**：M3技术规格在 `design/p0-m3-radiation-thermal` 设计分支，不在原来的main；该分支已包含所有已合并的M2代码。
2. 基于 `design/p0-m3-radiation-thermal` 创建工作分支 `feature/p0-m3-radiation-thermal`；不要基于旧main然后因看不到设计文件而自行发明规范。
3. 阅读 `AGENTS.md`（M3当前范围优先）、`docs/P0-M3_IMPLEMENTATION_SPEC.md`、`docs/03_DATA_CONTRACTS.md`、`docs/development/M2_REPORT.md`、`src/threebody/physics/nbody/models.py`、`src/threebody/experiments/simulation.py`、`src/threebody/world/models.py`。
4. 分里程碑依次完成 M3-01（轨道读取和校验）、M3-02（恒星/自转/日照）、M3-03（C0）、M3-04（C1）、M3-05（CLI、归档与CI）。每次变更保持 tests 通过。先给科学基准写测试再做实现，禁止把测试失败掩盖为跳过。
5. M3按旧M2产物读取四体初态和trajectory，不修改其物理语义或已存在命令。使用可校验的单独M3配置；本轮生成器尚未生成的自转参数必须由M3配置提供，不能静默硬编码。
6. 优先验证三恒星逆平方辐射、球面投影、经度平均和全球1/4定律、C0解析平衡、C1等面积有限体积纬向输送能量守恒，以及轨道Hermite插值节点再现。
7. 提供明确记录和稳定的JSON输出，不要把天气、潮汐、恒星掩食作为已实现功能。
8. 执行 `pytest -q`、`ruff check .`、`python -m compileall -q src tests`，并提供实际测试日志、示例运行结果、误差与运行耗时。
9. 更新 `docs/development/M3_REPORT.md` 和 `docs/DECISIONS.md`；必要时记录安全输入失败案例与数值限制。
10. 将实现推送到 `feature/p0-m3-radiation-thermal` 并创建面向 `main` 的Draft PR，**禁止自动合并**。如设计分支上的M3文档尚未并入main，Draft PR中应包含其提交。

完成交接时汇报：PR链接、Head SHA、测试摘要、C0/C1能量残差、近距离样本限制、遗留风险。
