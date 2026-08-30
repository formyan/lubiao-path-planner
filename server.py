# -*- coding: utf-8 -*-
"""
路标 · 本地 AI 服务

- 只在 127.0.0.1 本机运行，API 密钥只保存在本机，不会发送到浏览器；
- 提供静态页面（index.html 等）与两个接口：
    GET  /api/health   检测服务与密钥是否就绪
    POST /api/ai-plan  把用户输入转发给 DeepSeek，返回结构化规划
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"
MAX_BODY = 64 * 1024
TIMEOUT = 150
CONFIG_KEYS = ("DEEPSEEK_API_KEY", "AI_MODEL", "AI_PROXY", "PORT")


def read_env_file():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return ""
    for enc in ("utf-8-sig", "gbk", "utf-16"):
        try:
            return env_file.read_text(encoding=enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return ""


def refresh_env():
    """重新读取 .env：用户保存 / 修改密钥后无需重启服务即可生效。
    .env 中出现的键（即使为空）以 .env 为准；未出现的键保留进程环境变量。"""
    env_values = {}
    for raw in read_env_file().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k in CONFIG_KEYS:
            env_values[k] = v
    for k, v in env_values.items():
        os.environ[k] = v


def load_env():
    refresh_env()


load_env()


def api_key():
    return (os.environ.get("DEEPSEEK_API_KEY") or "").strip()


def model_name():
    return (os.environ.get("AI_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def ai_proxy():
    return (os.environ.get("AI_PROXY") or "").strip()


STAGE_LABELS = {
    "hs": "高中在读 / 高三",
    "hsgrad": "高中毕业（未入读本科）",
    "ug": "本科在读",
    "ugrad": "本科毕业",
    "ms": "硕士在读",
    "msgrad": "硕士毕业",
    "work": "职场人士",
    "other": "其他阶段",
}

GPA_LABELS = {
    "gpa-high": "3.8 及以上",
    "gpa-mid": "3.5–3.8",
    "gpa-low": "3.0–3.5",
    "gpa-min": "3.0 以下",
    "gpa-na": "暂无 / 不适用",
}

LANG_LABELS = {
    "lang-75": "雅思 7.5+ / 托福 110+",
    "lang-70": "雅思 7.0+ / 托福 100+",
    "lang-65": "雅思 6.5+ / 托福 90+",
    "lang-60": "雅思 6.0+ / 托福 80+",
    "lang-none": "尚无语言成绩",
}

EXP_LABELS = {
    "intern": "实习",
    "research": "科研 / 项目",
    "contest": "竞赛获奖",
    "exchange": "交换 / 海外经历",
    "work": "工作经历",
    "none": "暂无相关经历",
}

COUNTRY_LABELS = {
    "us": "美国",
    "uk": "英国",
    "sg": "新加坡",
    "hk": "中国香港",
    "au": "澳大利亚",
    "ca": "加拿大",
    "cn": "中国",
    "jp": "日本",
    "eu": "欧盟 / 欧洲",
}

MAJOR_LABELS = {
    "cs": "计算机 / 软件",
    "ds": "数据科学 / 统计",
    "fin": "金融 / 商科",
    "ee": "电子 / 电气",
    "media": "传媒",
    "law": "法律",
    "edu": "教育",
    "eng": "工程（机械 / 土木等）",
    "bio": "生物 / 医药",
    "other": "其他方向",
}

POSITION_LABELS = {
    "swe": "软件工程师",
    "pm": "产品经理",
    "da": "数据分析师",
    "consultant": "咨询顾问",
    "ibd": "投行分析师",
    "other": "其他岗位",
}

HORIZON_LABELS = {
    "1y": "1 年内（已临近）",
    "2y": "1–2 年",
    "3y": "2–3 年",
    "more": "3 年以上",
}

BUDGET_LABELS = {
    "b-na": "暂不确定",
    "b1": "10 万以内",
    "b2": "10–30 万",
    "b3": "30–60 万",
    "b4": "60 万以上",
}

PRIORITY_LABELS = {
    "steady": "稳妥上岸",
    "fast": "更快达成",
    "cheap": "成本更低",
    "name": "名校 / 名企牌子",
}


SYSTEM_PROMPT = (
    "你是资深的高等教育升学与职业规划顾问，擅长为中文用户设计具体、可执行的发展路径。\n"
    "你会：\n"
    "- 给出多条差异明显的路径（如直申、先就业再申请、桥梁 / 预科、保研 / 考研、转专业、社招跳槽等）；\n"
    "- 把每段路拆成「起点 → 关键节点 → 终点」，节点里写清可量化的达标要求（分数、时间、材料、流程）；\n"
    "- 区分事实与推断，不确定的信息注明「以官网为准」，不编造确定的录取率或薪资；\n"
    "- 记住：输出内容仅作参考，最终以目标院校 / 公司官网最新信息为准。"
)


def build_prompt(inp):
    lines = []
    lines.append("请为以下用户制定升学 / 职业发展路径。")
    lines.append("")
    lines.append("【用户当前状况】")
    lines.append("- 阶段：" + (STAGE_LABELS.get(inp.get("stage")) or "其他阶段"))
    if inp.get("school"):
        lines.append("- 院校 / 单位：" + str(inp.get("school")))
    if inp.get("major"):
        lines.append("- 专业 / 方向：" + str(inp.get("major")))
    if inp.get("gaokao"):
        lines.append("- 高考分数：" + str(inp.get("gaokao")) + " 分")
    if inp.get("gpa") and inp.get("gpa") != "gpa-na":
        lines.append("- GPA（4.0 制）：" + GPA_LABELS.get(inp.get("gpa"), ""))
    if inp.get("lang") and inp.get("lang") != "lang-none":
        lines.append("- 语言成绩：" + LANG_LABELS.get(inp.get("lang"), ""))
    exps = inp.get("exp") or []
    exps = [EXP_LABELS.get(e, e) for e in exps if e != "none"]
    lines.append("- 相关经历：" + ("、".join(exps) if exps else "暂无相关经历"))
    lines.append("")
    lines.append("【用户目标】")
    lines.append("- 填写规则：目标院校与目标职业均可填写；填写职业时职业为最终目标，院校作为路径中的关键节点（未填写时需推荐适配院校）；只填写院校时院校为最终目标。")
    uni_parts = []
    if inp.get("country"):
        uni_parts.append(COUNTRY_LABELS.get(inp.get("country"), inp.get("country")))
    if inp.get("university"):
        uni_parts.append(str(inp.get("university")))
    if inp.get("majorTarget"):
        uni_parts.append(MAJOR_LABELS.get(inp.get("majorTarget"), inp.get("majorTarget")))
    if uni_parts:
        lines.append("- 目标院校：" + " · ".join(uni_parts))
    else:
        lines.append("- 目标院校：未填写")
    job_parts = []
    if inp.get("company"):
        job_parts.append(str(inp.get("company")))
    if inp.get("position"):
        job_parts.append(POSITION_LABELS.get(inp.get("position"), inp.get("position")))
    if job_parts:
        lines.append("- 目标职业：" + " · ".join(job_parts))
    else:
        lines.append("- 目标职业：未填写")
    if inp.get("aiGoal"):
        lines.append("- 自定义目标描述：" + str(inp.get("aiGoal")))
    lines.append("")
    lines.append("【时间与偏好】")
    lines.append("- 距离关键时间点：" + HORIZON_LABELS.get(inp.get("horizon"), "待定"))
    lines.append("- 预算：" + BUDGET_LABELS.get(inp.get("budget"), "待定"))
    lines.append("- 最看重：" + PRIORITY_LABELS.get(inp.get("priority"), "稳妥上岸"))
    kb_text = build_kb_context_text(inp)
    if kb_text:
        lines.append("")
        lines.append(kb_text)
    lines.append("")
    lines.append(
        "要求：\n"
        "1. 给出 3–4 条发展路径，路径之间差异要明显（例如直申、先就业再申请、桥梁 / 预科、保研 / 考研、"
        "转专业、社招跳槽等）。\n"
        "2. 每条路径按「起点 → 关键节点 → 终点」组织，节点 4–7 个。\n"
        "3. 每个节点写清达标要求（分数、时间、材料、流程等），尽量可量化。\n"
        "4. 路径 meta 给出难度（高 / 中 / 低）、总时长、预计花费、成功率（高 / 中 / 低）、适合人群。\n"
        "5. 提供 3–4 条「如果遇到意外」的备选方案。\n"
        "6. 以公开常识为准，不确定处注明「以官网为准」，不要编造确定的录取率。\n"
        "7. 目标语义：若用户填写了目标职业，所有路径都必须以该职业为最终目标节点（goal），"
        "并在每条路径中包含院校节点——用户填写了目标院校就用该院校，未填写则推荐 2–3 所适配院校"
        "（节点类型 academic，标题如「适配院校建议」）。若用户只填写目标院校，则院校为最终目标，按留学路径规划。\n"
        "8. 只输出 JSON，不要输出 JSON 以外的任何文字。\n"
    )
    lines.append(
        "JSON 结构如下：\n"
        '{\n'
        '  "start": {"summary": "起点摘要"},\n'
        '  "goal": {"summary": "目标摘要"},\n'
        '  "paths": [\n'
        '    {\n'
        '      "title": "路径标题",\n'
        '      "tag": "推荐 / 稳妥 / 冲刺 / 性价比 或 null",\n'
        '      "intro": "路径简介",\n'
        '      "meta": {"difficulty": "高/中/低", "duration": "约 X 年", "cost": "约 X 万元", "success": "高/中/低", "suitable": "适合人群"},\n'
        '      "nodes": [\n'
        '        {\n'
        '          "type": "start/academic/language/exam/material/application/interview/admit/visa/grad/work/offer/skill/goal 之一",\n'
        '          "title": "节点标题",\n'
        '          "time": "约 X 个月 / 年",\n'
        '          "summary": "一句话说明",\n'
        '          "detail": [\n'
        '            {"label": "达标要求 / 院校信息 / 岗位信息 / 材料清单 / 时间节点 / 风险提示",\n'
        '             "icon": "check/grad/globe/clipboard/file/send/chat/checkcircle/plane/briefcase/star/zap/target/time 之一",\n'
        '             "items": [{"t": "具体内容", "key": true 或 false}]}\n'
        "          ]\n"
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ],\n"
        '  "planB": [{"title": "意外情况", "text": "应对建议"}]\n'
        "}"
    )
    return "\n".join(lines)


def build_kb_context_text(inp):
    """把本地知识库中与目标相关的数据放进提示词，让 AI 优先采用真实信息。"""
    kb = inp.get("kb")
    if not isinstance(kb, dict):
        return ""
    lines = []
    uni = kb.get("university")
    if isinstance(uni, dict) and uni.get("name"):
        lines.append("- 目标院校（本地知识库）：" + str(uni.get("name")) + (("（" + str(uni.get("en")) + "）") if uni.get("en") else ""))
        for key, label in (
            ("rank", "排名"), ("focus", "定位"), ("ielts", "雅思要求"), ("toefl", "托福要求"),
            ("gpa", "GPA 参考"), ("gre", "GRE / GMAT"), ("fee", "费用参考"),
            ("timeline", "申请时间线"), ("notes", "备注"),
        ):
            if uni.get(key):
                lines.append("    " + label + "：" + str(uni.get(key)))
    job = kb.get("job")
    if isinstance(job, dict) and job.get("name"):
        lines.append("- 目标岗位（本地知识库）：" + str(job.get("name")) + (("（" + str(job.get("en")) + "）") if job.get("en") else ""))
        if job.get("companies"):
            lines.append("    常见雇主：" + " / ".join(str(c) for c in job["companies"]))
        if job.get("edu"):
            lines.append("    学历参考：" + str(job.get("edu")))
        for s in (job.get("skills") or [])[:6]:
            lines.append("    核心技能：" + str(s))
        if job.get("interview"):
            lines.append("    面试参考：" + str(job.get("interview")))
    note = kb.get("companyNote")
    if note:
        lines.append("- 目标公司（本地知识库）：" + str(note))
    unis = kb.get("recommendedUnis")
    if isinstance(unis, list) and unis:
        lines.append("- 适配院校候选（本地知识库，按需选择）：")
        for u in unis[:3]:
            if isinstance(u, dict) and u.get("name"):
                gpa = str(u.get("gpa") or "").split("，")[0]
                lines.append("    " + str(u.get("name")) + ("：GPA " + gpa if gpa else "") + "；雅思 " + str(u.get("ielts") or ""))
    if not lines:
        return ""
    return "【本地知识库参考（请优先采用其中数据，未覆盖的信息再基于公开常识并注明以官网为准）】\n" + "\n".join(lines)


class AIError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def extract_text(data):
    parts = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "message" and isinstance(node.get("content"), list):
                for c in node["content"]:
                    if (
                        isinstance(c, dict)
                        and c.get("type") in ("output_text", "text")
                        and isinstance(c.get("text"), str)
                        and c["text"].strip()
                    ):
                        parts.append(c["text"].strip())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data.get("output") or data)
    return "\n".join(parts)


def parse_json(text):
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if m:
        text = m.group(1)
    else:
        i = text.find("{")
        j = text.rfind("}")
        if i >= 0 and j > i:
            text = text[i : j + 1]
    try:
        return json.loads(text)
    except Exception:
        return None


ALLOWED_NODE_TYPES = {
    "start", "academic", "language", "exam", "material", "application",
    "interview", "admit", "visa", "grad", "work", "offer", "skill", "goal",
}

ALLOWED_ICONS = {
    "check", "grad", "globe", "clipboard", "file", "send", "chat", "checkcircle",
    "plane", "briefcase", "star", "zap", "target", "time", "light", "flag",
}


def clean_str(v, limit, default=""):
    if not isinstance(v, str):
        v = "" if v is None else str(v)
    v = v.strip()
    return v[:limit] if v else default


def norm_items(items):
    out = []
    if isinstance(items, list):
        for it in items[:8]:
            if isinstance(it, dict) and clean_str(it.get("t"), 1):
                out.append({"t": clean_str(it.get("t"), 220), "key": bool(it.get("key"))})
            elif isinstance(it, str) and it.strip():
                out.append({"t": it.strip()[:220], "key": False})
    return out


def norm_detail(detail):
    out = []
    if isinstance(detail, list):
        for d in detail[:6]:
            if not isinstance(d, dict):
                continue
            label = clean_str(d.get("label"), 40)
            items = norm_items(d.get("items"))
            if not label or not items:
                continue
            icon = d.get("icon")
            icon = icon if isinstance(icon, str) and icon in ALLOWED_ICONS else "check"
            out.append({"label": label, "icon": icon, "items": items})
    return out


def norm_node(n):
    if not isinstance(n, dict):
        return None
    title = clean_str(n.get("title"), 80)
    if not title:
        return None
    ntype = n.get("type")
    ntype = ntype if isinstance(ntype, str) and ntype in ALLOWED_NODE_TYPES else "academic"
    return {
        "type": ntype,
        "title": title,
        "time": clean_str(n.get("time"), 40),
        "summary": clean_str(n.get("summary"), 500),
        "detail": norm_detail(n.get("detail")),
    }


def norm_path(p):
    if not isinstance(p, dict):
        return None
    title = clean_str(p.get("title"), 80)
    if not title:
        return None
    nodes = []
    if isinstance(p.get("nodes"), list):
        for n in p["nodes"][:9]:
            nn = norm_node(n)
            if nn:
                nodes.append(nn)
    if len(nodes) < 3:
        return None
    meta = p.get("meta") if isinstance(p.get("meta"), dict) else {}
    return {
        "title": title,
        "tag": clean_str(p.get("tag"), 20) or None,
        "intro": clean_str(p.get("intro"), 500),
        "meta": {
            "difficulty": clean_str(meta.get("difficulty"), 10, "中"),
            "duration": clean_str(meta.get("duration"), 30, "待定"),
            "cost": clean_str(meta.get("cost"), 40, "待定"),
            "success": clean_str(meta.get("success"), 10, "中"),
            "suitable": clean_str(meta.get("suitable"), 80, "根据个人情况判断"),
        },
        "nodes": nodes,
    }


def default_planb(inp):
    if inp.get("goalType") == "work":
        return [
            {"title": "直投受阻", "text": "先进入同行业次一级公司积累 1–2 年，再通过社招跳槽进入目标公司。"},
            {"title": "面试被挂", "text": "复盘每轮面试，针对薄弱环节专项训练，寻找内推或隔段时间再投。"},
            {"title": "简历关过不了", "text": "补充相关实习 / 项目经历，找内推绕过简历池，或先投相邻岗位积累经验。"},
        ]
    return [
        {"title": "申请被拒", "text": "转投同国梯队院校或换国家，部分项目有春季入学；也可先工作 / 深造一年再申请。"},
        {"title": "语言不达标", "text": "选择语言要求更匹配的项目，或走语言班 / 预科等桥梁路径，部分学校接受有条件录取。"},
        {"title": "预算超支", "text": "优先考虑性价比更高的地区，或申请奖学金、助研 / 助教岗位。"},
    ]


def extract_plan(text, inp):
    obj = parse_json(text)
    if not isinstance(obj, dict):
        raise AIError(502, "AI 返回的格式无法识别，请点击重试")
    start = obj.get("start") if isinstance(obj.get("start"), dict) else {}
    goal = obj.get("goal") if isinstance(obj.get("goal"), dict) else {}
    paths = []
    if isinstance(obj.get("paths"), list):
        for p in obj["paths"][:4]:
            np_ = norm_path(p)
            if np_:
                paths.append(np_)
    if len(paths) < 3:
        raise AIError(502, "AI 返回的路径不足 3 条，请点击重试")
    planb_raw = obj.get("planB")
    planb = []
    if isinstance(planb_raw, list):
        for b in planb_raw[:6]:
            if isinstance(b, dict):
                t = clean_str(b.get("title"), 40)
                x = clean_str(b.get("text"), 300)
                if t and x:
                    planb.append({"title": t, "text": x})
    if not planb:
        planb = default_planb(inp)
    if not any(p.get("tag") for p in paths):
        paths[0]["tag"] = "推荐"
    plan = {
        "start": {"title": "起点", "summary": clean_str(start.get("summary"), 200) or "当前状况"},
        "goal": {"title": "目标", "summary": clean_str(goal.get("summary"), 200) or "目标"},
        "paths": paths,
        "planB": planb,
    }
    enrich_plan_kb(plan, inp)
    return plan


def kb_uni_block(uni):
    pairs = (
        ("排名定位", " · ".join(x for x in (uni.get("rank"), uni.get("focus")) if x)),
        ("雅思要求", uni.get("ielts")),
        ("托福要求", uni.get("toefl")),
        ("GPA 参考", uni.get("gpa")),
        ("GRE / GMAT", uni.get("gre")),
        ("费用参考", uni.get("fee")),
        ("申请时间线", uni.get("timeline")),
        ("备注", uni.get("notes")),
    )
    items = [
        {"t": (k + "：" + v) if k and v else v, "key": k in ("排名定位", "GPA 参考")}
        for k, v in pairs
        if v
    ]
    if uni.get("source"):
        items.append({"t": "来源：" + str(uni.get("source")), "key": False})
    return {"label": "知识库 · 院校信息（2026-08）", "icon": "grad", "items": items[:10]}


def kb_job_block(job):
    items = [{"t": str(job.get("name")) + (("（" + str(job.get("en")) + "）") if job.get("en") else ""), "key": True}]
    if job.get("companies"):
        items.append({"t": "常见雇主：" + " / ".join(str(c) for c in job["companies"]), "key": False})
    if job.get("edu"):
        items.append({"t": "学历参考：" + str(job.get("edu")), "key": False})
    for s in (job.get("skills") or [])[:6]:
        items.append({"t": str(s), "key": True})
    return {"label": "知识库 · 岗位信息（2026-08）", "icon": "briefcase", "items": items[:10]}


def kb_unis_block(unis):
    items = []
    for u in unis[:3]:
        if isinstance(u, dict) and u.get("name"):
            gpa = str(u.get("gpa") or "").split("，")[0]
            items.append({
                "t": str(u.get("name")) + ("：GPA " + gpa if gpa else "") + "；雅思 " + str(u.get("ielts") or ""),
                "key": True,
            })
    items.append({"t": "以上为本地知识库中的适配院校候选，请以官网最新信息为准", "key": False})
    return {"label": "知识库 · 适配院校（2026-08）", "icon": "grad", "items": items[:6]}


def has_kb_block(node, prefix):
    return any((d.get("label") or "").startswith(prefix) for d in node.get("detail") or [])


def uni_match_keys(uni):
    keys = []
    if uni.get("name"):
        name = str(uni.get("name"))
        keys.append(name)
        short = name.replace("大学", "").replace("理工学院", "").replace("学院", "")
        if short and short != name:
            keys.append(short)
    if uni.get("en"):
        en = str(uni.get("en"))
        keys.append(en)
        first = en.split()[0] if en.split() else ""
        if first and len(first) >= 2:
            keys.append(first)
    return [k for k in keys if k]


def enrich_plan_kb(plan, inp):
    """把本地知识库信息作为可展开详情块，附加到 AI 返回的对应节点上。"""
    kb = inp.get("kb")
    if not isinstance(kb, dict):
        return plan
    uni = kb.get("university")
    job = kb.get("job")
    note = kb.get("companyNote")
    unis = kb.get("recommendedUnis")
    company = str(inp.get("company") or "")
    for path in plan.get("paths") or []:
        uni_done = job_done = note_done = False
        for node in path.get("nodes") or []:
            title = (node.get("title") or "") + " " + (node.get("summary") or "")
            ntype = node.get("type")
            if (
                uni and isinstance(uni, dict) and uni.get("name")
                and not uni_done and not has_kb_block(node, "知识库 · 院校信息")
                and any(k and k in title for k in uni_match_keys(uni))
            ):
                node.setdefault("detail", []).append(kb_uni_block(uni))
                uni_done = True
            if (
                job and isinstance(job, dict) and job.get("name")
                and not job_done and not has_kb_block(node, "知识库 · 岗位信息")
                and (ntype in ("skill", "work", "interview", "offer", "goal") or str(job.get("name")) in title)
            ):
                node.setdefault("detail", []).append(kb_job_block(job))
                job_done = True
            if (
                note and not note_done and not has_kb_block(node, "知识库 · 公司时间线")
                and (ntype == "application" or (company and company in title))
            ):
                node.setdefault("detail", []).append({
                    "label": "知识库 · 公司时间线（2026-08）",
                    "icon": "time",
                    "items": [{"t": str(note), "key": True}],
                })
                note_done = True
        # 兜底：路径中没有提到院校名称时，把院校信息挂到申请 / 录取 / 就读节点上
        if (
            uni and isinstance(uni, dict) and uni.get("name")
            and not uni_done
        ):
            for node in path.get("nodes") or []:
                if node.get("type") in ("application", "admit", "grad"):
                    node.setdefault("detail", []).append(kb_uni_block(uni))
                    uni_done = True
                    break
        if isinstance(unis, list) and unis:
            for node in path.get("nodes") or []:
                if (
                    not has_kb_block(node, "知识库 · 适配院校")
                    and ("适配院校" in (node.get("title") or "") or "院校建议" in (node.get("title") or ""))
                ):
                    node.setdefault("detail", []).append(kb_unis_block(unis))
                    break
    return plan


def ai_opener():
    proxy = ai_proxy()
    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )


def empty_summary(data):
    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list) and choices:
        c = choices[0]
        msg = c.get("message") or {}
        return (
            "finish_reason=" + str(c.get("finish_reason"))
            + ", content_len=" + str(len(msg.get("content") or ""))
            + ", reasoning_len=" + str(len(msg.get("reasoning_content") or ""))
        )
    return "choices=" + str(len(choices) if isinstance(choices, list) else "none")


def call_ai(inp, key):
    payload = {
        "model": model_name(),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(inp)},
        ],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        },
    )
    last_data = None
    for attempt in (1, 2):
        try:
            with ai_opener().open(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                err = json.loads(e.read().decode("utf-8"))
                detail = (err.get("error") or {}).get("message") or err.get("message") or ""
            except Exception:
                pass
            if e.code == 401:
                raise AIError(502, "DeepSeek 密钥无效或已过期，请检查 .env 中的 DEEPSEEK_API_KEY（应以 sk- 开头，不要有多余空格）")
            if e.code == 402:
                raise AIError(502, "DeepSeek 账户余额不足，请前往 DeepSeek 开放平台充值后重试")
            if e.code == 429:
                raise AIError(502, "AI 服务请求过于频繁或额度不足，请稍后再试")
            msg = detail or ("HTTP %d" % e.code)
            raise AIError(502, "AI 服务返回错误：" + msg[:300])
        except urllib.error.URLError as e:
            reason = str(e.reason or "未知原因")[:200]
            proxy = ai_proxy()
            hint = "；已配置代理 %s，请确认代理软件正在运行" % proxy if proxy else "；如直连不通，可在 .env 中配置 AI_PROXY 指向本地代理"
            raise AIError(502, "无法连接 AI 服务，请检查网络后重试（" + reason + hint + "）")
        except Exception:
            raise AIError(502, "AI 服务响应异常，请重试")
        text = extract_chat_text(data) or extract_text(data)
        if text:
            return extract_plan(text, inp)
        last_data = data
    raise AIError(502, "AI 返回内容为空，请重试（" + empty_summary(last_data) + "）")


def extract_chat_text(data):
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    msg = first.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
        return msg["content"].strip()
    return ""


MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
}


def safe_path(url_path):
    if url_path in ("/", ""):
        return ROOT / "index.html"
    if url_path.startswith("/api/"):
        return None
    rel = url_path.lstrip("/")
    if not rel or rel.startswith(".") or "\\" in rel:
        return None
    p = (ROOT / rel).resolve()
    try:
        p.relative_to(ROOT.resolve())
    except ValueError:
        return None
    if p.suffix.lower() not in MIME or not p.is_file():
        return None
    return p


_STATIC_CACHE = {}


def read_static(p):
    """带 mtime 校验的静态文件缓存，避免每个请求都重复读盘。"""
    try:
        st = p.stat()
        key = str(p)
        hit = _STATIC_CACHE.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
        data = p.read_bytes()
        if len(_STATIC_CACHE) > 32:
            _STATIC_CACHE.clear()
        _STATIC_CACHE[key] = (st.st_mtime_ns, st.st_size, data)
        return data
    except OSError:
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "LubiaoAI/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/health"):
            refresh_env()
            self.send_json(
                200,
                {"ok": True, "ai": bool(api_key()), "model": model_name(), "envFile": (ROOT / ".env").exists()},
            )
            return
        p = safe_path(self.path.split("?", 1)[0])
        if p is None:
            self.send_json(404, {"ok": False, "error": "页面不存在"})
            return
        data = read_static(p)
        if data is None:
            self.send_json(404, {"ok": False, "error": "读取文件失败"})
            return
        self.send_response(200)
        self.send_header("Content-Type", MIME[p.suffix.lower()])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/api/ai-plan":
            self.send_json(404, {"ok": False, "error": "接口不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.send_json(413, {"ok": False, "error": "请求内容过大"})
                return
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8"))
            inp = body.get("input")
            if not isinstance(inp, dict):
                raise ValueError("input missing")
            kb = body.get("kb")
            if isinstance(kb, dict):
                inp["kb"] = kb
        except Exception:
            self.send_json(400, {"ok": False, "error": "请求格式有误"})
            return
        refresh_env()
        key = api_key()
        if not key:
            self.send_json(
                503,
                {"ok": False, "error": "尚未配置 AI 密钥：请把 .env.example 复制为 .env，填入 DEEPSEEK_API_KEY 后重启服务"},
            )
            return
        try:
            plan = call_ai(inp, key)
        except AIError as e:
            self.send_json(e.status, {"ok": False, "error": e.message})
            return
        self.send_json(200, {"ok": True, "plan": plan, "model": model_name()})


class LubiaoServer(ThreadingHTTPServer):
    allow_reuse_address = False


def main():
    port = int(os.environ.get("PORT") or "8787")
    refresh_env()
    try:
        httpd = LubiaoServer(("127.0.0.1", port), Handler)
    except OSError:
        line = "=" * 54
        print(line)
        print("启动失败：端口 %d 已被占用。" % port)
        print("可能原因：已有旧的服务窗口在运行（密钥没有更新）。")
        print("解决办法：关闭所有旧的黑色命令行窗口后，重新双击「启动服务.bat」；")
        print("或在 .env 中加一行 PORT=其他端口号 后重启。")
        print(line)
        sys.exit(1)
    line = "=" * 54
    print(line)
    print("路标 · 本地 AI 服务已启动")
    print("本地地址： http://127.0.0.1:%d" % port)
    if api_key():
        print("AI 密钥：已配置 · 模型 " + model_name())
    else:
        print("AI 密钥：未配置（复制 .env.example 为 .env 并填入 DEEPSEEK_API_KEY 后重启）")
    if ai_proxy():
        print("网络代理：已配置 " + ai_proxy())
    print("提示：密钥只在本机使用，不会发送到浏览器")
    print("按 Ctrl+C 停止服务")
    print(line)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")


if __name__ == "__main__":
    main()
