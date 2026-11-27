---
name: weather-particle-motion
description: 为天光天气壁纸制作可离线播放的降水与风场动画。用于按前后景遮挡飘雨飘雪、按风向风速移动粒子，或在有接触依据的落点绘制飞溅、涟漪和配对滴水。
---

# 降水与风场动画

使用主 Agent 已确认的照片、分区和天气方案。读入 `subject`、`motion`、`merge_into` 和各档天气强度。场景沿用固定相机。高速主体的留存条件由上游表面层负责。

从项目根目录执行。构建使用项目的 Python 环境；播放只需本地浏览器或 WKWebView。

```sh
skill=skills/weather-particle-motion
"$skill/run.sh" "$PHOTO" --manifest "$REGIONS/manifest.json" --answers "$PLAN" \
  --handoff "$PHYSICS/handoff.json" --seed 731 --fps 20 --out "$OUTPUT"
open "$OUTPUT/index.html"
```

- `--handoff` 可重复，分别传详细雨天仿真、普通位置包。单张位置图中只有 255 表示允许积水。文字结论不会变成像素许可。直接受雨的已有水体单独判定。详细出口和落点还需在同一复核对中各有非零流量。
- 有材质时传 `--materials "/路径/materials.json"`，多档可重复。每档只接受一份清单，材质中的 answers_sha256 必须与传入的方案文件字节摘要一致。积雪层随天气选择固定图像。无材质时播放原图与空中粒子。
- 详细仿真自带深度。主 Agent 单独导出的远黑近白深度图可通过 `--depth` 指定。先核对它与照片所属作品；无独立绑定信息的裸图仅可校验方向、尺寸和记录摘要。只有分组时查看 `motion.json` 的 `group-two-layer` 精度记录。
- `--weather` 设置初始档，`--fps` 沿用宿主预算，缺省 20。输出目录需为新路径。未传物理或材质参数时会尝试方案同目录的 `天气积留/handoff.json`、`materials.json`。
- 本地嵌入、指定时间和横风的方法见 [运行接口](references/runtime.md)。保留模块、场景脚本和清单中引用的图片。接入已有渲染器时让此模块接管空气和接触动画，宿主的面板位于最上层。

## 离线验收

从项目根目录直接打开 [赛车](demo/race/index.html) 或 [海边](demo/shore/index.html)。赛车沿用高速主体方案和相对逆深度。海边回归样图按上游静止车辆情景设置，采用分组遮挡。可在预览中切换全部天气。

```sh
swift skills/weather-particle-motion/scripts/verify.swift \
  skills/weather-particle-motion/demo "$(mktemp -d)/验收"
```

此命令在独立 WKWebView 中打开交付页面，导出固定种子连续帧和像素检查结果。原始测量见 [验收记录](demo/evidence/report.json)。

生成的运行时按天气挂载材质。切换后可等待 setWeather 返回的 Promise 或 whenReady() 再检查最终表面。
