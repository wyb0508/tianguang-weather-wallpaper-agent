# 本地渲染接口

预览入口为 `index.html`。可选 `#desktop` 隐藏面板。WKWebView 用 `loadFileURL(_:allowingReadAccessTo:)`，读取范围设为当前包目录。模块为经典脚本，采样图由本地 JS 提供，运行时无需 fetch 或服务端。校验与合成在正向原图坐标系完成。横纵比例变化只改变 cover 变换，CSS 像素与 canvas 后备像素分别换算。

嵌入用一个具有明确宽高和相对定位的空容器。默认从当前页面目录读取照片和材质；嵌入其他目录时，baseURL 传带结尾斜杠的资源目录 URL。

```html
<div id="scene" style="position:relative;width:100%;height:100vh"></div>
<script src="motion.js"></script>
<script src="scene-data.js"></script>
<script>
async function setup() {
  const motion = await WeatherParticleMotion.create(
    document.getElementById('scene'), weatherMotionScene,
    {fps:20, quality:1, resolution:1}
  );
  motion.setWeather('大雨', {wind:-0.085});
  return motion;
}
window.motionReady = setup();
</script>
```

| 接口 | 单位与行为 |
| --- | --- |
| setWeather(name, {wind}) | wind 是带方向的水平速度分量，单位为原图高度/秒。正值向右，负值向左，0 为无横风。默认读取各档参数。换天气或风速从该档起点开始。 |
| resize(width, height, zoom) | 视口为 CSS 像素，zoom ≥ 1，中心裁切。容器大小改变时自动调整。 |
| pause() / resume() | 保留模拟时间，幂等。隐藏页和系统减少动态效果暂停时钟。 |
| sample(seconds) | 以种子重建该时刻并立即绘制，返回原图坐标的 air 和 impacts。离线采样先 pause。 |
| destroy() | 撤销回调、尺寸与系统监听，移除模块图层。相同容器再次 create 会回收旧实例。 |
| create 选项 | fps 为绘制预算，quality 为粒子数量比例 0.05–1，resolution 为每 CSS 像素的后备像素数 0.2–2。paused 允许静态初始化。onFrame({timestamp,time}) 分别给出绘制回调 rAF 毫秒时间戳与累计模拟秒数。 |

场景采用远黑近白逆深度，将 PNG 原值与粒子的 0–1 深度统一后逐像素裁剪。无深度时前、后层粒子分别位于 0.85、0.25，主体遮挡面在 128/255。涟漪逐像素裁至当前水域或获准水洼。两层模式用粒子深度估计落点高度，再沿轨迹进入许可像素；它提供图像平面的交点。详细仿真模式取雨线与连续深度面的首次交点，深度突变处仅作遮挡。配对滴水采用末态流量，沿同一出口与落点循环。物理蒙版与分析网格跨 ID 的采样保守略过。

`motion.json` 的 inputs 记录照片、方案、分区、物理包、通道与材质摘要。profiles 的 count 是整张原图的粒子预算，near 是近景抽样概率，opacity 控制可见度，fall 和 wind 以原图高度/秒记录。照片和材质在底层，接触响应在材质上方，空中粒子逐像素遮挡，最后恢复题字原像素。光照天气清空动态层，光照颜色由宿主或对应材质提供。

深度来自独立交接包时，`--depth` 也接受一份 JSON 描述。提供 `image_sha256`、`manifest_sha256`、`size`（原图正向尺寸）、`orientation: "exif-transposed"`、`encoding: "relative-inverse-depth-u8"`、`path` 及该 PNG 的 `sha256`。构建时逐项核对。使用主 Agent 标准作品目录且没有详细仿真深度时，自动读取方案旁的「相对深度.png」。

只剩原始分组 PNG 时，清单记录已校验的原图与各分组文件摘要。缺少类别图时 `region_verification` 为 `image-bound-group-masks-only`，原方案声明的分区签名单列，无法从剩余文件复算。如果确认的类别只占某张分组蒙版的一部分，需要该类别图或区块图才能定位。
