# M5B-04b：M5A真实轨道的恒星辐射与C0/C1热场联动

状态：独立Draft PR，针对已合并PR #14的main。未完成原P0全部验收。

## 已实现
- 新命令：`threebody world thermal-long --run <M5A目录> --config <M3热模型yaml> --out-dir <root>/environments/<run_id>`。
- 对**已完成且非碰撞终止**的M5A归档运行完整段/hash/检查点验证，采用真实采样位置/速度（≤5000样本）和分段间相邻样本的三次Hermite插值为现有M3恒星辐射、C0与C1求解器提供连续时间强迫。
- 输入采用M3严格热模型物理参数：行星自转轴、C0/C1物理模式、反照率、辐射率、热容、纬向扩散与数值积分容差。传统`input.initial_state`/`input.m2_archive`路径字段对M5A改用`--run`，不能混用或声称旧M2的来源哈希有效。
- **独立不可变派生归档**：`long_environment_manifest.json` schema 0.7.0；记录M5A run/world ID、物理与输出时间表hash、已提交段清单及每段sha256、M3配置hash、计算时间、模型及能量收支诊断、每个输出文件的sha256。
- 新API：`GET /api/v1/runs/{run_id}/environment?start_s=&end_s=&max_points=`，从`artifact_root/environments/<run_id>`读取。必须校验原始M5A链和全部派生产物；最多2000个界面显示采样点，C0/C1未配置则为null，不补造。
- Web中与轨道时间条联动展示三恒星辐照度及总辐射曲线、C0温度曲线、C1纬度×时间温度热图；支持与轨道相同的SI时间、观察速度不改变积分速度、显示最近派生采样与轨道时间差。

## 当前物理和功能边界
1. 首版只处理M5A `COMPLETED`。碰撞、PAUSED、FAILED的覆盖必须另行引入已提交窗口限定与可恢复的热积分状态，不可在终止之后推算。
2. 5000样本硬上限明确防止将百年、数十万采样一次性放进内存；下一迭代需增量C0/C1检查点、逐段环境产物与缓存查询。
3. M4环境事件仍采用旧M2/M3来源哈希验证流程；**此PR不把旧版M4报告伪装成M5A来源已校验**，后续需单独实现M5A环境分类适配。
4. 使用相邻M5A采样的Hermite轨道插值提供积分期间连续强迫，并记录插值假设；近距离恒星遭遇需提高M5A原始采样频率，按M3 `max_orbit_sample_interval_s`拒绝过稀数据。
5. 模型仍是三颗各向同性点光源、无食的辐射、C0全球灰体及C1纬度能量平衡，非真实地表天气、非宜居充分条件。

## 完成验收需要
```bash
pytest -q
ruff check .
python -m compileall -q src tests
```
Python 3.11/3.12 CI；至少一次真实G2.1→M5A短期多段→现有M3物理求解器→Web HTTP的集成测试；篡改来源/派生哈希必须拒绝；C0-only/C1-only必须正确显示缺项，能量收支与物理时间吻合；打开网页检查轨道/辐射/C0/C1时间轴同步。

## 后续
- M5B-04c：M5A→M4世界结局与暴露统计，保留ESCAPE_CANDIDATE非永久逃逸、碰撞终止以及未覆盖数据语义。
- M5B-05：对比、导出、浏览器E2E。
- M5B-06：长历史增量热场/任务复原和可靠性发布验收。
