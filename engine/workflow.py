"""Image-bound design answers, shared by Terminal and the chat entry point."""
import hashlib
import json
import sys

WEATHER = {
    "晴朗":"日间光照", "日出":"晨光", "日落":"夕照", "黄昏":"暮色", "深夜":"夜色",
    "多云":"天空云幕", "大风":"风中粒子", "小雨":"细雨和湿润反光", "大雨":"雨线、湿润反光和水纹",
    "小雪":"稀疏雪花与局部薄雪", "中雪":"连续降雪与斑块积雪",
    "大雪":"密集雪花与连片积雪", "暴雪":"高密度雪幕与更广的表面覆盖",
}
MOTION = {"stationary":"静止", "moving":"运动", "high_speed":"高速运动"}
GAIN = {"subtle":.65, "standard":1., "strong":1.25}


def group_numbers(manifest):
    numbers = {g["id"]: "group:"+g["key"] for g in manifest["groups"]}
    if len(numbers) != len(manifest["groups"]) or not all(type(i) is int and i>0 for i in numbers):
        raise ValueError("预览分组编号重复或无效")
    return numbers


def choose(question, choices, default=None, numbers=None, pick=None):
    print(question, flush=True)
    numbers = numbers if numbers is not None else dict(enumerate(choices, 1))
    by_key = {key: i for i,key in numbers.items()}
    for key, title in choices.items():
        prefix = f"{by_key[key]}. " if key in by_key else ""
        print(f"  {prefix}{title} ({key})")
    if pick:
        print("  输入 pick 在预览图上点选。")
    while True:
        value = input("请输入编号或选项名" + (f" [回车：{choices[default]}]" if default else "") + "：").strip()
        if not value and default:
            return default
        if value in choices:
            return value
        if value.isdecimal() and int(value) in numbers:
            return numbers[int(value)]
        if value == "pick" and pick:
            selected = pick()
            if selected in choices:
                return selected
        print("请选择列表中的一项。")


def draft(photo, signature=None):
    digest = hashlib.sha256(str(photo.size).encode() + photo.tobytes()).hexdigest()
    plan = dict(version=1, confirmed=[], weather_mode=None, image_sha256=digest, subject=None, motion=None, merge_into={},
                sheltered=[], ground_snow="natural", accumulation="fixed_per_intensity",
                weather={p:"standard" for p in WEATHER})
    if signature:
        plan["region_pack_sha256"] = signature
    return plan


def collect(photo, regions, saved, subject, motion, groups=None, signature=None, *, numbers=None,
            pick=None, highlight=None, checkpoint=None):
    plan = draft(photo, signature)
    if saved is not None:
        provided = json.loads(saved.read_text())
        if not isinstance(provided, dict) or provided.get("image_sha256") != plan["image_sha256"] or provided.get("version") != 1:
            raise ValueError("方案版本或图片不匹配，请对这张原图重新分区并确认。")
        bound = provided.get("region_pack_sha256")
        if bound and bound != signature:
            raise ValueError("方案对应另一份分区，请传入匹配的 --regions-manifest。")
        plan.update({k:v for k,v in provided.items() if k in plan and k != "region_pack_sha256"})
        if "confirmed" not in provided:
            # Old completed plans explicitly carry subject, motion and weather.
            completed = provided.get("subject") and (provided["subject"] == "none" or provided.get("motion"))
            plan["confirmed"] = ["merge","subject","motion","weather"] if completed else []
    confirmed = plan["confirmed"]
    if not isinstance(confirmed, list) or not all(isinstance(k,str) for k in confirmed):
        raise ValueError("方案的问答进度无效")
    def finish(key):
        if key not in confirmed:
            confirmed.append(key)
            if checkpoint:
                checkpoint(plan)
    choices = {r["label"]:r["name"] for r in regions}
    interactive = sys.stdin.isatty()
    merges = plan["merge_into"]
    if not isinstance(merges, dict):
        raise ValueError("merge_into 需要记录原标签与目标标签。")
    if interactive and "merge" not in confirmed:
        print("已识别区块：" + "、".join(choices.values()))
        while True:
            line = input("需合并时输入 原标签=目标标签（例如 floor=road），结束按回车：").strip()
            if not line:
                break
            a, _, b = line.partition("=")
            if a in choices and b in choices and a!=b and b not in merges and a not in merges.values():
                merges[a] = b
            else:
                print("请使用上面分区清单中的两个不同标签，并直接合入最终类别。")
    if any(a not in choices or b not in choices or a==b or b in merges for a,b in merges.items()):
        raise ValueError("合并必须使用当前图片的不同标签，并直接指向最终类别。")
    if not interactive and saved and "merge" not in confirmed:
        raise ValueError("区块合并尚待确认，请继续交互问答")
    finish("merge")
    choices = {k:v for k,v in choices.items() if k not in merges}
    choices.update({"group:" + key: "分区蒙版 " + key for key in (groups or {})})
    choices["salient"] = "视觉显著主体（类别难以确定时）"
    if subject and subject.isdecimal() and numbers:
        subject = numbers.get(int(subject),subject)
    if subject and subject != plan["subject"]:
        plan["motion"] = None
        confirmed[:] = [k for k in confirmed if k not in ("subject","motion")]
    plan["subject"] = subject or plan["subject"]
    plan["motion"] = motion or plan["motion"]
    subject_choices = dict(choices)
    if groups:
        subject_choices["none"] = "无主体的风景"
    for key,question,options in [
        ("subject","这张图的主主体是什么？",subject_choices),
        ("motion","确认的主体在画面中是运动还是静止？",MOTION),
    ]:
        if key == "motion" and plan["subject"] == "none":
            plan["motion"] = None
            finish("motion")
            continue
        supplied = subject if key == "subject" else motion
        if saved and not interactive and key not in confirmed and not supplied:
            raise ValueError(question + " 请在问答中确认。")
        if interactive and not supplied and (plan[key] is None or key not in confirmed):
            plan[key] = choose(question, options, numbers=numbers if key=="subject" else None,
                               pick=pick if key=="subject" else None)
        if plan[key] not in options:
            raise ValueError(question + f" 请在问答中确认并填写 --{key} 或图片对应的方案。")
        if key=="subject" and highlight and interactive and "motion" not in confirmed:
            highlight(plan[key], merges)
        finish(key)
    if interactive and "weather" not in confirmed:
        details = plan.get("weather_mode") or choose("如何配置这张图的天气？",{"detail":"逐种天气设置","standard":"使用标准天气方案"},"standard")
        plan["weather_mode"] = details
        if details == "detail":
            print("可指定不直接淋雨、落雪的区块。")
            while "shelter" not in confirmed:
                reply = input("输入受遮挡标签，用英文逗号分隔；露天按回车：")
                sheltered = [k.strip() for k in reply.replace("，",",").split(",") if k.strip()]
                if all(k in choices for k in sheltered):
                    plan["sheltered"] = sheltered
                    finish("shelter")
                    break
                print("可选标签：" + "、".join(choices))
            if "ground" not in confirmed and any(k in choices for k in ("road","floor","earth","path","sidewalk","land","runway")):
                plan["ground_snow"] = choose("地面在下雪时如何留雪？",{"wet":"湿润、少量留雪","natural":"按材质与坡度留雪","cold":"冷地面、较易留雪"},"natural")
                finish("ground")
            for preset, effect in WEATHER.items():
                if preset in confirmed:
                    continue
                plan["weather"][preset] = choose(f"{preset}时，{effect}要多明显？",
                                                 {"subtle":"轻微","standard":"标准","strong":"明显"},"standard")
                finish(preset)
    if not interactive and saved and "weather" not in confirmed:
        raise ValueError("天气方案尚待确认，请继续交互问答")
    if (not isinstance(plan["sheltered"],list) or any(k not in choices for k in plan["sheltered"])
        or plan["ground_snow"] not in ("wet","natural","cold")
        or plan["accumulation"]!="fixed_per_intensity" or not isinstance(plan["weather"],dict)
        or set(plan["weather"])!=set(WEATHER) or any(v not in GAIN for v in plan["weather"].values())):
        raise ValueError("天气方案无效，请核对遮挡区块、地面状态和各天气强度。")
    finish("weather")
    return plan
