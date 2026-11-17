# M5B-03 — 本地真实任务控制（阶段性开发报告）

Branch: `feature/p0-m5b03-task-control`。本分支从已合并M5A与M5B-01/02的main创建。

## 实现
- 新增 `src/threebody/web/jobs.py`：单Worker线程顺序运行M5A `start_long_run` /`resume_long_run`，每次最多提交一个安全物理segment；局部任务状态与REBOUND权威归档状态分开报告。
- 新增 `POST /api/v1/runs`：严格校验 `WorldInitialState` 与 `LongRunSettings`，不覆盖既有run目录，返回HTTP 202（表示已排队，不表示积分完成）。
- 新增 `GET /api/v1/jobs/{run_id}`：显示控制任务状态、已提交段数及归档中的真实`simulation_time_s`。
- 新增 `POST /api/v1/runs/{run_id}/{pause,resume,cancel}`：请求暂停/取消须等**当前完整segment结束**；resume只允许M5A已经持久化`PAUSED`的run；cancel在安全暂停后转换持久化M5A状态为`CANCELLED`。
- Web前端新增JSON初态、JSON长跑配置粘贴输入和真实任务控件，保持旧的实验配置验证与归档浏览功能。
- 复用REBOUND原有M2 guard/事件/回放机制；不手动更改坐标和速度，不在碰撞后继续积分。

## 有意保留的限制
- 单用户、仅loopback绑定，单进程单Worker；多个服务进程同时启动仍由M5A跨进程writer lock阻止同一run双写。无云端认证或多租户能力。
- 页面刷新后，持久M5A run清单仍在；内存`/jobs`状态重置，已暂停的run可重新`resume`。进程崩溃运行中的检查点需要M5A既有显式审核/恢复机制，不能自动抢锁。
- 暂停/取消**不能在任意IAS15内部时间点即时生效**；可能需要等长segment结束，服务器关闭也可能等待正在运行的物理segment。
- 本版本没有网页生成初态、任务幂等键、跨进程持久任务队列、完整VIS2流式嵌入、M3/M4增量运行、运行报告导出；后续归属M5B-04~06。
- 不声称本分支经本地完整测试；应以Draft PR的Python3.11/3.12 CI为验收依据。

## 验收清单
```bash
pytest -q
ruff check .
python -m compileall -q src tests
```
要求既有M5B只读接口测试和新任务控制回归全部通过，外加一次真实小规模G2.1→M5A多段Web启动/暂停/恢复/取消的HTTP流程。后续M5B-04需加分段只读轨迹/事件范围API和VIS2集成。

## 科学边界
UI中的“模拟时间”永远来自已持久化M5A运行清单，SI秒。HTTP 202只代表任务请求被接受；浏览器播放速度不改变IAS15时间积分。世界毁灭、碰撞和飞离事件不得被“任务控制”机制抹除。
