# P0 M5B-04c — 可复现随机世界→真实积分→鼠标3D→M4世界结局

## 本分支目标与实际代码
基于已合并M5B-04b的main。实现本地单用户完整实验的主要前端路径：
1. UI编辑G2.1 bounded/unrestricted、seed、候选数量、初始速度/距离/逃逸代理比例、偏心率；保留JSON完整科学配置编辑。
2. POST /api/v1/worldsets调用**已有**严格配置解析及G2.1随机圆锥初态生成器，生成的世界和metadata不可变保存到`artifact-root/worldsets/<batch_id>`；包含生成失败/初始接触结局，不根据存活偏好隐藏世界。选择前S0及SHA-256校验，初始接触世界禁止积分。
3. 用户选择候选、模拟时长/采样间隔（SI秒），POST /api/v1/worlds/run从不可变初态直接创建真实REBOUND IAS15 M5A工作任务；请求接受并不等于物理积分完成。前端观察时钟与物理积分时钟独立。
4. 3D观测canvas新增Pointer Events鼠标按住拖动旋转，滚轮缩放；保持四体三维坐标经过视角投影，事件时间轴与M3派生温度联动。只画已归档轨迹，不推断未计算未来。
5. 新`threebody world assess-long --run <M5A> [--environment <派生M3>] --config configs/m4_world_outcomes.yaml --out-dir <root>/outcomes/<run_id>`；调用原M4分类/阈值和事件模型，使用真正M5A的已提交轨道与事件以及经过独立hash验证的M3派生档案。**内存适配的M4Inputs结构不存储为旧M2归档**，输出自有0.8.0 manifest、来源哈希、统计和事件完整性哈希。C1-only/无C0时不误报温和；ESCAPE_CANDIDATE不是已证实永久逃逸。
6. Web GET /api/v1/runs/{run_id}/outcome验证原轨道/派生热模型/产物的完整hash链，展示动力学与环境标签，把M4环境事件叠加到共同SI时间轴。

## 关键验收
- 同配置同seed/index可复现相同hash；真实HTTP生成→选候选→运行60s IAS15→轨道归档/时间轴；
- 上限、S0、初始接触、篡改及路径穿越拒绝；无需把客户端提交的四体任意坐标直接当作用户选择候选。
- M5A→M3→M4派生物理数据保留world_id、初始hash、物理/时间表hash、源segments和独立文件hash；变动则拒绝。
- 测试缺温度报告DATA_INSUFFICIENT；源真实C0/C1后M4统计采用原物理模型。
- Python3.11/3.12运行pytest、ruff、compileall，并在浏览器手工验证拖动/滚轮与UI布局。**不要把只做源码/CI视为已通过浏览器人工验收。**

## 仍待P0关闭前处理
- 新世界生成候选以本地单用户/≤20候选为限；真实积分时长上限10儒略年、最大20k输出样本。
- M5B-04b当前热派生只支持已完成、未碰撞、≤5000样本；必须明示并在Web中显示缺失而非预测。
- 目前热计算、M4分析经CLI显式触发；新世界生成后不会谎称“自动已计算温度”。可将安全的异步分析Worker及资源预算纳入M5B-05/06。
- 需要真正的浏览器E2E（拖动、滚轮、事件跳转、不同大小视口）、模拟任务重启后可靠恢复、实验比较/导出及大轨迹流式热积分，才可最终关闭P0。
- 长期层级三星世界生成预设可单独增加，但不能以筛选稳定世界替代纯物理随机初态。

## 建议演示
```sh
threebody web serve --artifact-root runs --host 127.0.0.1 --port 8765
# 浏览器打开 http://127.0.0.1:8765
# 生成世界→选择可积分候选→选时长→启动→完成后在轨道观察区读取
# (若需要物理温度与结局，使用下面命令；参数与归档必须匹配)
threebody world thermal-long --run runs/my-world-001 --config configs/m3_thermal.yaml --out-dir runs/environments/my-world-001
threebody world assess-long --run runs/my-world-001 --environment runs/environments/my-world-001 --config configs/m4_world_outcomes.yaml --out-dir runs/outcomes/my-world-001
```
