# M5B-06 — 本地P0发布可靠性及浏览器端到端验收

状态：开发PR，基于M5B-05功能分支，必须在PR #17合并后重对齐main。**只有CI和真实Chromium E2E均通过才可考虑P0发布候选**。

## 实现
1. 重启后的`GET /api/v1/jobs/<id>`从M5A持久运行清单恢复只读可见状态：`RUNNING`标记`RECOVERY_REQUIRED`，`PAUSED`标记可继续，`COMPLETED/COLLISION`保持真实终态；不会因内存作业记录丢失就谎报404。
2. `POST /api/v1/runs/<id>/resume`允许已暂停或中断的`RUNNING`，真正调用M5A的校验/锁/二进制检查点恢复机制；如另有活动writer则M5A拒绝，绝不抢锁或仅用四体JSON重建轨道。
3. `POST /api/v1/runs/<id>/analyze`仅针对科学终态，恢复/重新执行已有的M3/M4来源校验后处理；重复分析请求不会引起多个同时写者。UI提供“读取持久运行状态”和“重新验证/分析科学数据”。
4. 退出服务先请求安全分段暂停，拒绝关闭中提交的新任务；如果物理恰好在退出过程中完成，分析标记`DEFERRED_SHUTDOWN`，通过显式重试完成，不在ThreadPool关闭后强行提交后台任务。
5. 把默认科学YAML纳入wheel安装包。正式Web配置、初始化与子资源不会依赖仓库当前工作目录。
6. 新`tests/test_m5b_restart_recovery.py`：真实REBOUND分段存档跨三个不同的Web控制器实例，恢复直至真实终态、后处理，校验非法ID/不可访问档案。
7. 新`tests/test_m5b_browser.py` + `.github/workflows/browser.yml`：真实Chromium点击生成G2.1候选、60秒IAS15实验、等待自动M3/M4、鼠标拖动轨道canvas、比较、JSON报告下载与解析。

## 物理与运营门槛
- 所有数据以服务端M5A SI物理时钟为准；显示加速仅UI，不修改IAS15积分。
- 只对真实归档可验证数据做导出，行星毁灭后不续算、不虚构热模型。
- 首版部署为本地单用户，非鉴权公网/多人服务；Web server必须绑定loopback。
- 持久M5A二进制检查点和段写锁是唯一可信恢复方式，内存队列是提示，不是计算状态真相。
- M3单次派生仍限制`COMPLETED`且≤5000轨道样本；超过预算时热场显式`UNAVAILABLE`，M4不得误称温和。该限制是**P0已知能力边界**，不是支持任意模拟时长全部温度的保证。
- 不自动解除可能尚活跃的物理锁，不吞掉分析失败原因；崩溃重启可能需用户点击继续/重新分析。

## P0发布前检查
- Python3.11/3.12 pytest, ruff, compileall 全绿。
- 独立Chromium E2E全绿，包括3D鼠标真实事件（不是直接调用JS画布函数）、报告下载。
- 完整wheel安装后`importlib.resources.files("threebody.web.static")`能读到`m3_default.yaml`、`m4_default.yaml`。
- Web真实模拟→观察→归档→来源匹配热模型与M4结局，碰撞世界和无温度世界正确披露。
- 故障注入及长期数值守恒沿用M5A已合并测试；在多进程崩溃/锁残留、磁盘配额及不可恢复检查点场景进行人工验收后方可最终发布。
- 百万级样本的端到端增量热模型**不在此版本已实现范围内**，后续需要真正增量M3 checkpoint，不应作P0完成声称。

## 典型使用
```sh
pip install -e '.[dev,physics,viz]'
mkdir -p runs
threebody web serve --artifact-root runs --host 127.0.0.1 --port 8765
# http://127.0.0.1:8765
pytest -q
ruff check .
python -m compileall -q src tests
# 真实浏览器验收：
pip install 'playwright>=1.55,<2'
python -m playwright install chromium
pytest -q tests/test_m5b_browser.py -m browser
```

状态决策：05/06通过各自CI及独立浏览器测试并合并后，按P0明确范围可以宣布“本地科学实验控制台P0候选完成”；仍不能承诺分布式多人、无限长温度计算、碰撞后物理或真实宜居性。
