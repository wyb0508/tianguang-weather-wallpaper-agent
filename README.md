# 天光天气壁纸 Agent

输入一张照片，生成能随天气变化的 macOS 桌面壁纸，以及可独立打开的交互预览。

## 使用

- 在 Codex 中打开整个项目，上传原图并说「把这张图做成天气壁纸」。
- 在 Finder 中双击 **生成天气壁纸.command**，选择照片。生成完成后自动打开预览。
- 在预览中切换大雪、降雨、日出等效果。点击 **识别区域** 查看分区和面积。要启用桌面版本，打开同一作品目录里的 **动态天气壁纸.app**，可在菜单栏里切换效果或退出。

每次生成独立的作品目录。HTML 已内嵌全部图层，可以单独复制给别人。桌面应用也可单独复制，接收方需要 macOS。生成器使用 Python 3.10–3.13，并会在首用时按需安装依赖；模型随包附带。应用采用本机临时签名，没有开发者公证。

```sh
./生成天气壁纸.command "/路径/照片.heic" --name "雪中山湖"
./生成天气壁纸.command "/路径/照片.jpg" --preview-only
```

## 图像与特效

U²-NetP 提取显著主体，SegFormer-B0 对场景进行语义分割。生成器根据主体软蒙版、顶部轮廓、局部亮度和边缘估算积雪位置。地面积雪较连贯，枝叶和山石的积雪沿纹理分布。远处粒子会被主体遮住，近处粒子从主体前经过。水面降雨会出现受水域蒙版限制的涟漪。天空另有云层色调。纯风景可能没有独立主体，复杂轮廓和反光表面可能分错。

模型仅在生成作品时运行。壁纸运行时使用预先生成的图层，雨雪动画最高每秒刷新 20 次。独立 HTML 手动演示天气。macOS 应用经系统定位授权后向 Open-Meteo 请求所在区域的天气和日出日落时间，并支持菜单栏手动预览。照片不参与天气请求。

## 模型来源

- U²-NetP 出自 [U-2-Net](https://github.com/xuebinqin/U-2-Net)，使用 [rembg 发布的 ONNX 权重](https://github.com/danielgatis/rembg/releases/tag/v0.0.0)。随附条款见 [Apache License 2.0](models/LICENSE-U2Net.txt)。
- SegFormer-B0 ADE20K 使用 [NVIDIA 模型](https://huggingface.co/nvidia/segformer-b0-finetuned-ade-512-512) 的 [量化 ONNX 转换](https://huggingface.co/Xenova/segformer-b0-finetuned-ade-512-512/tree/d3e5499fa8701ff0453ca940a8dfeae39b2f1504)。NVIDIA 许可将该模型的用途限定为非商业研究或评估。完整条款见 [SegFormer 许可](models/LICENSE-SegFormer.txt)。

## 天气积留位置技能

读取已分区照片，导出积雪、积水、霜、露与冰的原尺寸位置蒙版。支持单图和分区 ZIP 批处理。[源码与使用说明](skills/weather-surface-physics/README.md) · [下载完整技能包](skills/weather-surface-physics.zip)
