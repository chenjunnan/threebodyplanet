# M5B-05 — 自动科学派生、世界比较与报告导出

## 交付范围
- 后台M5A任务结束后，顺序调用已有M3辐射/C0/C1与M4科学分类；分析状态与物理积分状态独立记录，自动派生工作不会伪造模拟完成。
- 配置通过web包内封装的版本化M3/M4 YAML；生成到`root/environments/<run_id>`、`root/outcomes/<run_id>`，不可覆盖已有派生文件，已存在时强制检查全部来源与产物哈希。
- `COMPLETED`时尝试M3/M4；`COLLISION`时仅调用M4动力学分类，热数据明确不适用；M3输入超出5000样本或过稀时报告`UNAVAILABLE`，M4依旧输出`DATA_INSUFFICIENT`而不是温和结论。
- `GET /api/v1/experiments/compare?runs=A,B`和`/report?runs=A,B`最多8个run，遍历验证真实M5A段链、源哈希、M3/M4产物后再返回世界状态、事件统计、温度覆盖及分类；缺少证据为null。JSON报告带内容摘要，前端下载独立不可变快照。
- UI完成积分后持续轮询科学派生，提供运行ID多选对比表与JSON导出。

## 测试与验收
`pytest -q`、`ruff check .`、`python -m compileall -q src tests`，Python3.11/3.12；真实HTTP生成世界→IAS15完成→自动M3/C0/C1→M4→compare/report，非法run ID、重复ID、超过数量、篡改段hash均拒绝。结论需以实际CI为准。

## 保留限制
单机单用户，内存任务队列。M3一次处理上限5000个轨道采样；长期热积分分段增量及重复进程异常恢复将在M5B-06硬化。科学分析失败不会回滚已经完成的动力学模拟，也不能被UI伪装为完整多物理场结局。真实浏览器鼠标交互与导出E2E作为M5B-06验收门槛。
