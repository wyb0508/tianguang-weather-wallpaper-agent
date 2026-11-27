---
name: scene-regions
description: 识别照片或插画的主体和场景大区块，生成彩色编号预览、与原图对齐的分区蒙版，以及兼容天光天气壁纸 Agent 类别 ID 的 JSON 清单。用于图片分区、同类对象合并和向特效工具交付图层。
---

# 图像主体与区块识别

目标是让下游能直接选中有意义的画面区域施加效果。根据**这一张图**决定大区块。同种对象默认合在一起，例如三辆赛车共用一张主体蒙版。没有明显主角的风景可以只有天空、水面和山林。道路、人行道、小径、草地、泥土、沙地等地表默认合并为一个 `ground`，先展示这个合并结果。`regions` 和 `classes.png` 保留原始材质。树冠、灌木、山体和水面单独判断。大片暗色背景、树隙、图框与文字需要结合原图判断。具有明显版式功能的题字区、信息栏或角标可独立归入 `caption`。明确要求单选某种材质时可用 `--group` 拆出该组。

## 运行与核对

1. 看原图并记录能看见的主体和主要表面。用本技能目录里的脚本执行本地识别。输出位置选一个新目录。

   ```sh
   "$SKILL_DIR/run.sh" "/路径/图片.jpg" --out "/路径/首次分区"
   ```

   `SKILL_DIR` 指本文件所在目录。脚本优先复用项目 Python 环境。独立使用时，可用 Python 3.10–3.13 在技能目录创建 `.venv` 并用其 pip 安装同目录的 `requirements.txt`。模型随技能提供。默认 `--quality precise` 使用 SegFormer B0/B2 识别整图，用原图和显著性轮廓校正初始边缘。随后 CascadePSP 在 512 像素长边上逐组细化表面，SlimSAM 在 1024 像素长边上由各组内部的正反提示点生成候选。相邻组在轮廓附近竞争像素，天空采用吻合原分组的候选。主体按原分组与两个候选的多数意见调整，并保护分离的同类实例。邻近已识别蓝天的树叶缝隙可按原图色差细化。把投影留给承接它的表面，把物体上的反射留给物体。快速预览可用 `--quality fast`。复用编号、区块 key 或拆分方案时，使用与首次预览相同的质量模式和修正文件。

2. 阅读 `manifest.json`，对照原图看 `preview.jpg` 的大区块及 `regions-preview.jpg` 的细分边界。若有 `model_disagreement.preview`，查看其中的玫红区域。颜色表示两个模型对可能影响分组的像素给出了不同类别，`fraction` 表示占分析图的面积。两者也可能同时判断错误，仍要对照原图。内部核对围绕各大区块共用的边界进行，检查相邻区域有无漏分或混入。显著性与语义的交并比用于解释候选依据。它不是准确率。自动结果中的 `review_salient` 需要核对。无明确主角的风景可直接用 `--subject none` 导出。若有两个合理的主体解释或分组会改变后续效果，展示合适的彩色编号图，说明具体差别，每次只问一个影响结果的问题。对象属于同一主体类别时直接合并。
3. 语义清楚时直接导出。按当前图片实际出现的标签重新分组。例如核实室外赛车图的 `floor` 和 `road` 都是赛道后，可用下面的命令。普通道路归入地面。若底部有清晰的拍摄信息栏，在分析图核对首行后传入 `--footer-start 行号`，使整条信息栏独立分组。湖面和山林同样可以通过 `--group` 命名。清单中的 `groups[].key` 是供程序消费的英文键。

   ```sh
   "$SKILL_DIR/run.sh" "$IMAGE" --out "$FINAL_DIR" --subject car --group track=road,floor
   ```

   `--subject` 接受本图类别、区块 key、编号或以逗号分开的多个值。`salient` 使用显著性轮廓。当视觉上明确的错分只占某个标签的一部分，按下文的局部修正流程处理。下游需要拆开同类时，可先用 `--split-class car` 生成新的细分预览，再保留同一拆分参数，用该预览的 key 指定归属（如 `--subject car.1,car.4`）。拆分依据是不相连的像素，车身、尾翼或反光都可能各成一块，须核对原图。接触的同类物体可能连在一起。局部边缘仍不准时，在交付中指出具体位置。
4. 测试或查看识别结果时，每张图默认只展示整图分组预览 `preview.jpg` 和分组名称，依照本图内容命名。仅在用户明确要求检查某一区块的边界时，展示相应的单独轮廓或局部放大图。需要交接蒙版时附上清单或分区目录。若上一步提问，等用户回答后再交付最终分组。识别任务到这里结束。后续的主体动静和天气设置由壁纸 Agent 询问。

## 局部修正

例如赛道和护栏共享了一部分 `road` 像素，整类重分会连带改变正确的赛道。对照原图与分歧图，在已辨认的护栏范围画多边形，只改其中的 `road` 或 `floor`。从第一次导出的清单复制 `sha256` 和 `analysis_size`，按本图像素填写坐标。以下坐标仅说明文件格式。

```json
{"sha256":"复制本图指纹","analysis_size":[1024,576],
 "edits":[{"from":["road","floor"],"to":"fence","polygon":[[680,320],[810,330],[810,352],[680,340]]}]}
```

在相同命令上加 `--corrections "/路径/修正.json"`，导出到新目录。程序按列表顺序在分析网格上改类别，再重新计算主体、区块、蒙版和清单。坐标原点为预览图像左上角，避开下方图例。目标类别须来自 ADE20K 词表，并能在原图上得到确认。核对新预览和 `corrections.changed_pixels` 后交付。视觉上无法确定的区域，按上面的规则向用户提问。

### 原图像素级修正

细枝或文字笔画可用 `--group-edits "/路径/精修.json"` 交接已核实的边界。先查看本图整图预览，再依据原图制作所需的局部二值 PNG。每张 PNG 都与应用 EXIF 旋转后的原图同尺寸，白色像素改归目标组，黑色像素保持当前归属。蒙版路径相对于精修清单。目标组须已存在，按列表顺序执行，最后一次编辑决定重叠部分的归属。原来的模型类别会从目标组内最近的原区块延展。把两侧都改准时分别提供增加和收回的蒙版。例如核实右下角是印刷题字，可先用 `--group caption=signboard` 建组，再把字间露出的地面归还 `ground`。只在当张图片核实后使用这些选项。

```json
{"sha256":"本图原文件的指纹","edits":[{"group":"ground","mask":"字间地面.png"},{"group":"caption","mask":"文字笔画.png"}]}
```

## 蒙版交接

`manifest.json` 使用 `scene-regions/v1`。所有路径相对于清单目录。下游按 `groups[].key` 查找 `mask` 路径。白色 255 代表区块内，黑色 0 代表区块外。各组互斥，合起来覆盖全图。`subject.region_ids` 为空表示本次没有主体。`sha256` 绑定原始文件字节。

- `classes.png` 的像素值是 **ADE20K 类别 ID**，与壁纸 Agent 的「区块类别.png」一致，包括 ID 0 的墙面。局部修正会把对应像素改成目标类别 ID。
- `regions.png` 的像素值对应本清单的 `regions[].id`。通过 `class_id` 找到原始类别，通过 `groups[].region_ids` 找到大区块。区块编号仅在本次输出内有效。
- PNG 坐标以原图应用 EXIF 旋转后的左上角为原点，尺寸见 `size`。语义分析网格见 `analysis_size`，最长边 1024。精细模式在 `boundary_refinement.working_size` 对应的图像上调整边缘（最长边 3200），导出时从同一份区块 ID 图生成所有原图尺寸的蒙版。预览图下沿附有图例。
- 壁纸 Agent 可用 `./生成天气壁纸.command "$IMAGE" --regions-manifest "$FINAL_DIR/manifest.json" --regions-only` 复用这次分区。后续传入同一份 `--regions-manifest` 和确认后的 `--answers`。主体选择 `group:subject` 会使用已核对的主体蒙版，无主体风景可选 `none`。这一步由接手的 Agent 在制作壁纸时执行。

本地语义模型使用 SegFormer-B0 和 SegFormer-B2，视觉显著性由 U²-NetP 提供，许可文件分别在 `models/LICENSE-SegFormer.txt` 和 `models/LICENSE-U2Net.txt`。随包 SegFormer 权重的许可范围为非商业研究或评估。[B2 模型来源](https://huggingface.co/nvidia/segformer-b2-finetuned-ade-512-512) · [量化 ONNX 发布版本](https://huggingface.co/Xenova/segformer-b2-finetuned-ade-512-512/tree/df795789e70f4089c8658907679c6fd2367c89a5)

精细边界权重随包提供，本地 CPU 可直接推理。SlimSAM 量化图的版本为 `5850ab45`，CascadePSP 图的版本为 `a6b21ce1`，外部张量已展开为 ONNX Runtime 可读取的 FP32 文件。许可位于 `models/refiners/`。[SlimSAM 权重](https://huggingface.co/Xenova/slimsam-77-uniform/tree/5850ab45f587c112167512ffef949107115e26a0) · [CascadePSP 权重](https://huggingface.co/hkchengrex/CascadePSP-onnx/tree/a6b21ce1d485c1208650473234775114873d881c)

供主 Agent 交互使用时保留 groups.id；带图例的 preview.jpg 与终端分组编号共用这一字段。
