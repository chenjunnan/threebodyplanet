# ADR M6.3 — WebGL 三体星观察器：架构与科学数据契约

状态：**M6.3-a/b/c 已实现；d/e 仍待实施；默认保留 Canvas**
依赖：M5A真实分段轨道归档、M3经过验证的辐射和C0/C1、M4结局与事件、M6.1热检查点、M6.2诊断接口。  
版本目标：M6.3-alpha。此ADR不能修改已发布科学归档语义。

## 1. 目标及非目标

目标：在浏览器中自由旋转、缩放、点选三恒星与行星，按真实物理时间回放已经提交的轨道、辐射和纬度温度；容许可见性增强的视觉效果，但不得伪造轨迹、光度、未来轨道或碰撞后的演化。

非目标：实时GPU N体模拟、将Canvas绘制坐标当作物理状态、计算大气天气、单纯根据色彩判定宜居、默认引入大型第三方资源CDN、多人联网。

## 2. 模块分层

```text
Verified M5A archive ── GET /runs/{id}/trajectory ─────┐
Verified M5A events  ── GET /runs/{id}/events ──────────┤
Verified M3 archive ─── GET /runs/{id}/environment ─────┤
Verified M4 archive ─── GET /runs/{id}/outcome ─────────┤
Job status & provenance GET /jobs/{id} ──────────────────┤
                                                        ▼
  ScienceArchiveAdapter (only versioned, verified source; explicit nulls)
       │                               │
       ▼                               ▼
  PhysicalTimeline                  BodyMetadata
  SI seconds / exact data           radius, mass, spin and obliquity
       │                               │
       ▼                               ▼
  ScenePresenter (pure calculations; no writes to science archive)
       │  render-frame [{id, xyz, r_display, color, sample_time, quality}]
       ▼
  RendererAdapter  ─── WebGL renderer (candidate Three.js, locally pinned)
       └─────────── Legacy Canvas fallback and WebGL context-loss recovery
```

**唯一权威物理时钟：** M5A `t_s`。帧率、播放倍率和渲染刷新时间不得改变或推进真实物理时间。物理输入由服务端已验证文件提供，不能让WebGL引擎积分四体系统。

## 3. 数据契约（第一期不新增破坏性API）

| 输入 | 来源 | 主要字段 | 验证/约束 |
| --- | --- | --- | --- |
| 四体位置 | `GET /api/v1/runs/{run_id}/trajectory` | `world_id`, `sample_count`, `samples[*].t_s`, `bodies[*].position_m` | 单位 SI m、严格时间递增、不可超出已提交区间 |
| 事件 | `.../events` | `t_s`, `event_type`, `body_ids`, `observed` | 必须关联本次 M5A 来源；碰撞后不可延长 |
| M3 | `.../environment` | `rows[*].t_s`, `flux_w_m2`, `c0_temperature_k`, `c1_latitude_rad`, `c1_temperature_k` | 缺失为 null；`sampling_quality`及 `sampling_warning` 保留 |
| M4 | `.../outcome` | 世界结局与来源哈希 | 不把 M4 推断当作轨道位置 |
| 运行状态 | `GET /api/v1/jobs/{run_id}` | 已提交物理区间、阶段及科学派生状态 | 不根据排队/运行中状态画未来轨迹 |

实际首版需要验证服务端时间范围查询、分页/降采样的现有契约；严禁客户端把降采样数据反推为高频轨道。若后续需要恒星质量、半径及自转轴，则添加**独立、只读、具哈希来源的身体元数据接口**，不得改写 M5A 轨迹格式；在该接口完成前不展示未经验证的物理半径。

### 时间对齐规则

- 将选中的 **SI物理时刻**映射到当前已下载轨道样本；轨道轨迹由已验证点连接。默认显示真实点，不虚构未计算的时间。
- **可选视觉插值模式**：仅限两个真实相邻且距离/速度有效、没有穿越终态事件的采样之间，标记为 `DISPLAY_INTERPOLATED`，其位置**不是**IAS15真实积分结果，不允许写回科学归档、碰撞判断、辐照推导或M4分类。
- M3与M5A不一定具有相同视图降采样索引；通过独立`t_s`查找最近的**真实M3采样**。时间差须可见，不做温度或辐照外推。
- 碰撞终态只绘制至真实碰撞时刻；粒子融合、闪光为可开关视觉特效且必须标注“示意”，不是粒子物理状态。

## 4. 坐标、相机、尺度

1. 惯性坐标 `BARYCENTRIC_INERTIAL`、SI米为唯一源空间；渲染空间采用指定正交轴映射，版本化记录（例如 `[x, z, -y]`）。
2. 对每个可见时间窗选取**固定渲染原点**（例如窗口中心质心），高精度先在CPU双精度世界坐标减原点，再转换为GPU单精度坐标；切换窗口时显式更新原点。
3. 距离用统一可配置换算 `m_to_scene_units`；保留`真实尺度`与`增强尺寸`双模式。增强尺寸只影响球体/光晕渲染，不影响位置、碰撞或引力计算。
4. 星体显示色：首版可用版本化主序星视觉色表；不得声称等于真实光谱观测。自转轴用世界存档来源中的方向，未归档则隐藏。
5. 相机：orbit control、聚焦单个天体/系统、自由缩放、重置视角、停止播放、键盘可达。物理运动与相机运动分离。
6. 大动态范围：对数可视化图层是**视觉映射**，绝不可改变辐照度数值或者温度标签。

## 5. 性能预算与技术选型

- RendererAdapter抽象，使 `WebGL` 与旧 `Canvas` 可以交换；默认保留Canvas，用户实验性开启WebGL。
- 候选 Three.js/自研轻量 WebGL2 对比基准后再确定依赖版本和打包方式；如果采用库，版本固定、资源本地服务，不依赖运行时CDN。
- 目标：典型桌面4天体、最多2000显示采样点、静态视角帧率目标60fps；低端GPU/设备自适应降低轨迹线细节，不减少物理归档真实性。
- 一帧不能在主线程解析上万行JSON；数据解析/索引缓存与渲染分离。轨迹窗口按现有API分批加载，缓存以`run_id + source_hash + time_window`为键。
- WebGL上下文丢失恢复、WebGL不可用、浏览器降低运动模式时自动退回现有Canvas，不应导致任务控制丢失。

## 6. 拆分交付

| 增量 | 范围 | 完成标准 |
| --- | --- | --- |
| M6.3-a | 科学归档适配器+时间轴类型及验证 | 只读、拒绝跨世界混用、无时间外推 |
| M6.3-b | WebGL渲染器实验开关+Canvas回退 | 真实Chromium E2E覆盖WebGL可用和模拟不可用 |
| M6.3-c | 自由相机、天体点选、轨迹回放 | 与已记录物理时间/坐标一致 |
| M6.3-d | 恒星视觉材质、行星C1热图球体 | 温度等面积纬带与M3一致、色标与真实数值联动 |
| M6.3-e | 碰撞事件与事件跳转、性能基准 | 碰撞后的视觉示意不能伪造新的物理状态 |

## 7. 验收及质量门

- 3D同屏四体位置与M5A源数据在坐标映射误差预算内一致；真实世界质量/速度/位置提示由归档提供。
- 轨道和温度完全缺失时显示“无数据”，不绘制上次任务的缓存。
- 未来时刻、物理碰撞后续、跨运行ID拼接均明确拒绝；降采样误差和热科学质量可见。
- 真实Chromium的鼠标拖拽、滚轮、键盘、时间滑块、近距与碰撞事件、fallback及WebGL context loss至少各有一项覆盖。
- 保留旧M5A/M3/M4及G2.1归档schema和再现性；升级仅改变Web展示，不改变任何已核验科学结果。

**实施状态（2026-11-17）：** a/b 已合并；c 增加共用相机、点选聚焦、键盘操作和有界真实采样回放，WebGL 仍为用户主动启用的实验选项。d/e 不在本增量中；不把科学源数据格式绑定到特定渲染库。
