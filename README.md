# 天光天气壁纸 Agent

输入一张照片，生成能随天气变化的 macOS 桌面壁纸，以及可独立打开的交互预览。

[下载完整离线生成器](https://github.com/wyb0508/tianguang-weather-wallpaper-agent/releases/latest)。从源码运行时请使用 Git LFS 拉取精细分区模型权重。

## 使用

- 在 Codex 中打开整个项目，上传原图并说「把这张图做成天气壁纸」。
- 在 Finder 中双击 **生成天气壁纸.command**，选择照片。先看分区预览，在终端确认区块合并、主主体和动静状态，再选择标准方案或逐种天气配置。生成完成后自动打开壁纸预览。
- 在预览中切换大雪、降雨、日出等效果。点击 **识别分区** 查看带编号的分区预览。要启用桌面版本，打开同一作品目录里的 **动态天气壁纸.app**，可在菜单栏里切换效果或退出。

每次生成独立的作品目录。HTML 已内嵌全部图层，可以单独复制给别人。桌面应用也可单独复制，接收方需要 macOS。生成器使用 Python 3.10–3.13，并会在首用时按需安装依赖；模型随包附带。应用采用本机临时签名，没有开发者公证。

```sh
./生成天气壁纸.command "/路径/照片.heic" --name "雪中山湖"
./生成天气壁纸.command "/路径/照片.jpg" --preview-only
```

## 图像与特效

可用 `--regions-only` 单独导出分区图、逐像素类别和这张图的「天气方案.json」。问答完成后，同一张图可用 `--answers "/路径/天气方案.json"` 复现作品。程序校验图片指纹和类别。非交互环境使用方案文件，或明确传入 `--subject` 和 `--motion` 使用标准天气设置。若路面被分为 floor 和 road，可确认添加 `"merge_into": {"floor": "road"}`。每档积雪固定，飘雪独立播放。

SegFormer 区分场景材料，U²-NetP 可提取显著主体。Depth Anything V2 Small 在本地估计单张照片的相对深度。程序据此近似朝上表面和上方遮挡，再根据材质、主体运动状态和天气选择计算留雪与湿润程度。树木和草坪独立处理，天空与水面没有表面积雪。选择某个区块作为主体时，它仍保留原来的材质。每份作品附有「相对深度.png」「朝上表面估计.png」和逐区块的响应统计，便于检查误判。使用类别作为主体会选中该类别的全部像素。单张照片不能恢复准确的三维模型、真实温度或隐藏结构，透明、反光和倾斜拍摄的表面误差会更明显。

模型仅在生成作品时运行。壁纸运行时使用预先生成的图层，雨雪动画最高每秒刷新 20 次。独立 HTML 手动演示天气。macOS 应用经系统定位授权后向 Open-Meteo 请求所在区域的天气和日出日落时间，并支持菜单栏手动预览。照片不参与天气请求。

## 模型来源

- U²-NetP 出自 [U-2-Net](https://github.com/xuebinqin/U-2-Net)，使用 [rembg 发布的 ONNX 权重](https://github.com/danielgatis/rembg/releases/tag/v0.0.0)。随附条款见 [Apache License 2.0](models/LICENSE-U2Net.txt)。
- SegFormer-B0 ADE20K 使用 [NVIDIA 模型](https://huggingface.co/nvidia/segformer-b0-finetuned-ade-512-512) 的 [量化 ONNX 转换](https://huggingface.co/Xenova/segformer-b0-finetuned-ade-512-512/tree/d3e5499fa8701ff0453ca940a8dfeae39b2f1504)。NVIDIA 许可将该模型的用途限定为非商业研究或评估。完整条款见 [SegFormer 许可](models/LICENSE-SegFormer.txt)。

- 相对深度使用 [Depth Anything V2 Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf) 的 [量化 ONNX 权重](https://huggingface.co/onnx-community/depth-anything-v2-small/tree/4472b7362082ad9968fee890ca0f1e5aca36b93d)，许可为 Apache 2.0。

## 从分区到本地部署

1. `scene-regions` 输出编号预览和原图尺寸的区块。确认合并、主体、动静状态以及天气方案后进入仿真。
2. `weather-surface-physics` 将遮挡、运动和地面条件带入计算。小雨、大雨和四档降雪各有一份物理交接。积留位置分为允许、禁止和待核实。
3. `weather-material-renderer` 为 13 档天气分别交付清单，仅在具有空间证据的位置生成相应的表面材质。无降水天气的清单可以为空。
4. `weather-particle-motion` 校验图层所属的照片、分区和天气方案，使用相对深度绘制空气粒子及雨点接触效果。预览和应用内嵌同一份动画。实时风按气象来向和风速投影为画面横风，默认画面右侧代表东。缺少可用预报时采用本地时段光照。
5. `wallpaper-qa` 核对每档材质、动画、预览和签名后的应用。macOS 会启动隔离验收实例，使用当前屏幕尺寸回放固定天气数据，跳过系统定位。作品中的「验收/qa-report.json」记录逐天气像素检查和原生帧采样。「desktop_verified」表示通过本次原生验收，「preview_verified」表示通过预览资产验收，「needs_review」列出缺少的证据，「failed」列出失败原因。

已有分区使用 `--regions-manifest`。已核对的部位多边形可通过 `--surface-review` 输入。复用 `--physics-handoff` 时，外部许可与本次方案的位置许可取交集。要独立核对实际绘出的积雪，使用 `--geometry-reference`，其格式见 [赛车抽查样本](skills/wallpaper-qa/references/3f625bce07cce99213a4130f7e835c2f78f0d7cbeb3bc0f305406201ff13568f.json)。坐标基于 EXIF 正向原图，矩形右边和下边不包含在内。新照片可以先生成预览，再添加经目视确认的样本。每次修改方案后重新生成一份作品。

## 单独识别图片分区

图像分区 [Skill](skills/scene-regions/SKILL.md) 导出带编号的彩色预览、原图尺寸的分组蒙版和 `manifest.json`。同类对象默认合并。道路、人行道、草地和泥土会自动归为「地面」。精细模式会用 CascadePSP 和 SlimSAM 核对相邻组的边界，保留同组的分离实例，并提供整图分组预览。快速模式可加 `--quality fast`，对照原图核实的局部错分可用 `--corrections` 修正。壁纸生成器可以通过 `--regions-manifest` 导入分区，沿用颜色预览中核对过的主体与赛道边界。确认生成方案时，`group:subject` 指向分组后的主体，原始语义标签仍可选用。

```sh
skills/scene-regions/run.sh "/路径/照片.jpg" --out "/路径/照片分区"
./生成天气壁纸.command "/路径/照片.jpg" --regions-manifest "/路径/照片分区/manifest.json" --regions-only
```

## 天气积留位置技能

读取已分区照片，导出积雪、积水、霜、露与冰的原尺寸位置蒙版。支持单图和分区 ZIP 批处理。[源码与使用说明](skills/weather-surface-physics/README.md)

## 续答、位置判断和验收

终端主体问题里的数字与「区块预览.jpg」的分组编号一致。输入 `pick` 可在浏览器内点选同一蒙版，也可输入 `car` 等类别。确认时查看「已选主体.jpg」，再回答动静问题。分区草稿和问答中断后打印的 JSON 均可通过 `--answers` 继续。逐天气配置会保留已答的天气，旧版已完成方案仍可重放。

车辆表面按可见轮廓、明暗和朝向推断疑似玻璃与承托面。积水反射可采用路面内有凹陷和反射证据的区域，也可采用周围为路面且内部纹理明显更平滑的浅水区域。透明、深色或遮挡严重的表面仍可通过 `--surface-review` 复核。

验收会在独立浏览器中切换实际 HTML。可使用 Chrome、Node 和 Playwright，macOS 也支持 Swift 与 WebKit 回退。缺少探针会记录待复核。材质随天气载入，切换时释放旧绘图表面。报告分别列出可归属的宿主、WebContent 和独立 Chromium 进程 RSS。GPU 进程的 RSS 与显存分配量使用不同口径。
