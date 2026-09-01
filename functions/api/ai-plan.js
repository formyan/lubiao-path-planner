/**
 * EdgeOne Pages Function：/api/ai-plan
 *
 * 把前端输入转发给 DeepSeek，返回结构化的升学 / 职业路径。
 * - 密钥来自环境变量 DEEPSEEK_API_KEY，只存在于服务端；
 * - 内置：每 IP 限流（RATE_LIMIT，默认 10 次/小时）、每日总配额（DAILY_QUOTA，默认 200 次）、
 *   可选演示访问码（DEMO_ACCESS_CODE，设置后前端需带 X-Demo-Code 请求头）。
 *
 * 说明：限流 / 配额基于边缘函数内存（尽力而为），如需跨节点精确统计，
 * 可接入项目 KV（绑定后把变量名改为 LUBIAO_KV 并在此处使用）。
 */

const API_URL = "https://api.deepseek.com/chat/completions";
const DEFAULT_MODEL = "deepseek-v4-flash";

/* 内存限流与配额（单节点尽力而为） */
const hits = new Map(); // ip -> { start, count }
const daily = { date: "", count: 0 };

const STAGE_LABELS = {
  hs: "高中在读 / 高三", hsgrad: "高中毕业（未入读本科）", ug: "本科在读",
  ugrad: "本科毕业", ms: "硕士在读", msgrad: "硕士毕业", work: "职场人士", other: "其他阶段",
};
const GPA_LABELS = {
  "gpa-high": "3.8 及以上", "gpa-mid": "3.5–3.8", "gpa-low": "3.0–3.5",
  "gpa-min": "3.0 以下", "gpa-na": "暂无 / 不适用",
};
const LANG_LABELS = {
  "lang-75": "雅思 7.5+ / 托福 110+", "lang-70": "雅思 7.0+ / 托福 100+",
  "lang-65": "雅思 6.5+ / 托福 90+", "lang-60": "雅思 6.0+ / 托福 80+",
  "lang-none": "尚无语言成绩",
};
const EXP_LABELS = {
  intern: "实习", research: "科研 / 项目", contest: "竞赛获奖",
  exchange: "交换 / 海外经历", work: "工作经历", none: "暂无相关经历",
};
const COUNTRY_LABELS = {
  us: "美国", uk: "英国", sg: "新加坡", hk: "中国香港", au: "澳大利亚",
  ca: "加拿大", cn: "中国", jp: "日本", eu: "欧盟 / 欧洲",
};
const MAJOR_LABELS = {
  cs: "计算机 / 软件", ds: "数据科学 / 统计", fin: "金融 / 商科", ee: "电子 / 电气",
  media: "传媒", law: "法律", edu: "教育", eng: "工程（机械 / 土木等）",
  bio: "生物 / 医药", other: "其他方向",
};
const POSITION_LABELS = {
  swe: "软件工程师", pm: "产品经理", da: "数据分析师", consultant: "咨询顾问",
  ibd: "投行分析师", ai: "人工智能工程师", ux: "交互 / UI 设计师", game: "游戏策划",
  mech: "机械 / 汽车工程师", cyber: "网络安全工程师", ops: "产品运营",
  quant: "量化研究员", biotech: "生物医药研发", marketing: "市场营销 / 品牌经理",
  hr: "人力资源（HRBP / 招聘）", other: "其他岗位",
};
const HORIZON_LABELS = { "1y": "1 年内（已临近）", "2y": "1–2 年", "3y": "2–3 年", more: "3 年以上" };
const BUDGET_LABELS = {
  "b-na": "暂不确定", b1: "10 万以内", b2: "10–30 万", b3: "30–60 万", b4: "60 万以上",
};
const PRIORITY_LABELS = {
  steady: "稳妥上岸", fast: "更快达成", cheap: "成本更低", name: "名校 / 名企牌子",
};

const SYSTEM_PROMPT =
  "你是资深的高等教育升学与职业规划顾问，擅长为中文用户设计具体、可执行的发展路径。\n" +
  "你会：\n" +
  "- 给出多条差异明显的路径（如直申、先就业再申请、桥梁 / 预科、保研 / 考研、转专业、社招跳槽等）；\n" +
  "- 把每段路拆成「起点 → 关键节点 → 终点」，节点里写清可量化的达标要求（分数、时间、材料、流程）；\n" +
  "- 区分事实与推断，不确定的信息注明「以官网为准」，不编造确定的录取率或薪资；\n" +
  "- 记住：输出内容仅作参考，最终以目标院校 / 公司官网最新信息为准。";

function pick(map, key, fallback) {
  return map[key] || fallback;
}

function buildPrompt(inp) {
  const lines = [];
  lines.push("请为以下用户制定升学 / 职业发展路径。", "");
  lines.push("【用户当前状况】");
  lines.push("- 阶段：" + pick(STAGE_LABELS, inp.stage, "其他阶段"));
  if (inp.school) lines.push("- 院校 / 单位：" + inp.school);
  if (inp.major) lines.push("- 专业 / 方向：" + inp.major);
  if (inp.gaokao) lines.push("- 高考分数：" + inp.gaokao + " 分");
  if (inp.gpa && inp.gpa !== "gpa-na") lines.push("- GPA（4.0 制）：" + pick(GPA_LABELS, inp.gpa, ""));
  if (inp.lang && inp.lang !== "lang-none") lines.push("- 语言成绩：" + pick(LANG_LABELS, inp.lang, ""));
  const exps = (inp.exp || []).filter(e => e !== "none").map(e => pick(EXP_LABELS, e, e));
  lines.push("- 相关经历：" + (exps.length ? exps.join("、") : "暂无相关经历"));
  lines.push("", "【用户目标】");
  lines.push("- 填写规则：目标院校与目标职业均可填写；填写职业时职业为最终目标，院校作为路径中的关键节点（未填写时需推荐适配院校）；只填写院校时院校为最终目标。");
  const uniParts = [];
  if (inp.country) uniParts.push(pick(COUNTRY_LABELS, inp.country, inp.country));
  if (inp.university) uniParts.push(String(inp.university));
  if (inp.majorTarget) uniParts.push(pick(MAJOR_LABELS, inp.majorTarget, inp.majorTarget));
  lines.push(uniParts.length ? "- 目标院校：" + uniParts.join(" · ") : "- 目标院校：未填写");
  const jobParts = [];
  if (inp.company) jobParts.push(String(inp.company));
  if (inp.position) jobParts.push(pick(POSITION_LABELS, inp.position, inp.position));
  lines.push(jobParts.length ? "- 目标职业：" + jobParts.join(" · ") : "- 目标职业：未填写");
  if (inp.aiGoal) lines.push("- 自定义目标描述：" + inp.aiGoal);
  lines.push("", "【时间与偏好】");
  lines.push("- 距离关键时间点：" + pick(HORIZON_LABELS, inp.horizon, "待定"));
  lines.push("- 预算：" + pick(BUDGET_LABELS, inp.budget, "待定"));
  lines.push("- 最看重：" + pick(PRIORITY_LABELS, inp.priority, "稳妥上岸"));
  const kbText = buildKbContextText(inp.kb);
  if (kbText) lines.push("", kbText);
  lines.push("",
    "要求：\n" +
    "1. 给出 3–4 条发展路径，路径之间差异要明显（例如直申、先就业再申请、桥梁 / 预科、保研 / 考研、转专业、社招跳槽等）。\n" +
    "2. 每条路径按「起点 → 关键节点 → 终点」组织，节点 4–7 个。\n" +
    "3. 每个节点写清达标要求（分数、时间、材料、流程等），尽量可量化。\n" +
    "4. 路径 meta 给出难度（高 / 中 / 低）、总时长、预计花费、成功率（高 / 中 / 低）、适合人群。\n" +
    "5. 提供 3–4 条「如果遇到意外」的备选方案。\n" +
    "6. 以公开常识为准，不确定处注明「以官网为准」，不要编造确定的录取率。\n" +
    "7. 目标语义：若用户填写了目标职业，所有路径都必须以该职业为最终目标节点（goal），并在每条路径中包含院校节点——用户填写了目标院校就用该院校，未填写则推荐 2–3 所适配院校（节点类型 academic，标题如「适配院校建议」）。若用户只填写目标院校，则院校为最终目标，按留学路径规划。\n" +
    "8. 只输出 JSON，不要输出 JSON 以外的任何文字。\n",
    "JSON 结构如下：\n" +
    '{\n' +
    '  "start": {"summary": "起点摘要"},\n' +
    '  "goal": {"summary": "目标摘要"},\n' +
    '  "paths": [\n' +
    '    {\n' +
    '      "title": "路径标题",\n' +
    '      "tag": "推荐 / 稳妥 / 冲刺 / 性价比 或 null",\n' +
    '      "intro": "路径简介",\n' +
    '      "meta": {"difficulty": "高/中/低", "duration": "约 X 年", "cost": "约 X 万元", "success": "高/中/低", "suitable": "适合人群"},\n' +
    '      "nodes": [\n' +
    '        {\n' +
    '          "type": "start/academic/language/exam/material/application/interview/admit/visa/grad/work/offer/skill/goal 之一",\n' +
    '          "title": "节点标题",\n' +
    '          "time": "约 X 个月 / 年",\n' +
    '          "summary": "一句话说明",\n' +
    '          "detail": [\n' +
    '            {"label": "达标要求 / 院校信息 / 岗位信息 / 材料清单 / 时间节点 / 风险提示",\n' +
    '             "icon": "check/grad/globe/clipboard/file/send/chat/checkcircle/plane/briefcase/star/zap/target/time 之一",\n' +
    '             "items": [{"t": "具体内容", "key": true 或 false}]}\n' +
    "          ]\n" +
    "        }\n" +
    "      ]\n" +
    "    }\n" +
    "  ],\n" +
    '  "planB": [{"title": "意外情况", "text": "应对建议"}]\n' +
    "}"
  );
  return lines.join("\n");
}

function buildKbContextText(kb) {
  if (!kb || typeof kb !== "object") return "";
  const lines = [];
  const uni = kb.university;
  if (uni && uni.name) {
    lines.push("- 目标院校（本地知识库）：" + uni.name + (uni.en ? "（" + uni.en + "）" : ""));
    [
      ["rank", "排名"], ["focus", "定位"], ["ielts", "雅思要求"], ["toefl", "托福要求"],
      ["gpa", "GPA 参考"], ["gre", "GRE / GMAT"], ["fee", "费用参考"],
      ["timeline", "申请时间线"], ["notes", "备注"],
    ].forEach(([key, label]) => {
      if (uni[key]) lines.push("    " + label + "：" + uni[key]);
    });
  }
  const job = kb.job;
  if (job && job.name) {
    lines.push("- 目标岗位（本地知识库）：" + job.name + (job.en ? "（" + job.en + "）" : ""));
    if (job.companies && job.companies.length) lines.push("    常见雇主：" + job.companies.join(" / "));
    if (job.edu) lines.push("    学历参考：" + job.edu);
    (job.skills || []).slice(0, 6).forEach(s => lines.push("    核心技能：" + s));
    if (job.interview) lines.push("    面试参考：" + job.interview);
  }
  if (kb.companyNote) lines.push("- 目标公司（本地知识库）：" + kb.companyNote);
  if (Array.isArray(kb.recommendedUnis) && kb.recommendedUnis.length) {
    lines.push("- 适配院校候选（本地知识库，按需选择）：");
    kb.recommendedUnis.slice(0, 3).forEach(u => {
      if (u && u.name) {
        const gpa = String(u.gpa || "").split("，")[0];
        lines.push("    " + u.name + (gpa ? "：GPA " + gpa : "") + "；雅思 " + (u.ielts || ""));
      }
    });
  }
  if (!lines.length) return "";
  return "【本地知识库参考（请优先采用其中数据，未覆盖的信息再基于公开常识并注明以官网为准）】\n" + lines.join("\n");
}

function extractChatText(data) {
  const choices = data && Array.isArray(data.choices) ? data.choices : [];
  if (!choices.length || !choices[0].message) return "";
  const content = choices[0].message.content;
  return typeof content === "string" ? content.trim() : "";
}

function parseJson(text) {
  const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fence) text = fence[1];
  else {
    const i = text.indexOf("{");
    const j = text.lastIndexOf("}");
    if (i >= 0 && j > i) text = text.slice(i, j + 1);
  }
  try {
    return JSON.parse(text);
  } catch (e) {
    return null;
  }
}

const ALLOWED_NODE_TYPES = new Set([
  "start", "academic", "language", "exam", "material", "application",
  "interview", "admit", "visa", "grad", "work", "offer", "skill", "goal",
]);
const ALLOWED_ICONS = new Set([
  "check", "grad", "globe", "clipboard", "file", "send", "chat", "checkcircle",
  "plane", "briefcase", "star", "zap", "target", "time", "light", "flag",
]);

function cleanStr(v, limit, fallback = "") {
  if (typeof v !== "string") v = v === null || v === undefined ? "" : String(v);
  v = v.trim();
  return v ? v.slice(0, limit) : fallback;
}

function normItems(items) {
  const out = [];
  if (Array.isArray(items)) {
    items.slice(0, 8).forEach(it => {
      if (it && typeof it === "object" && it.t) out.push({ t: cleanStr(it.t, 220), key: !!it.key });
      else if (typeof it === "string" && it.trim()) out.push({ t: it.trim().slice(0, 220), key: false });
    });
  }
  return out;
}

function normDetail(detail) {
  const out = [];
  if (Array.isArray(detail)) {
    detail.slice(0, 6).forEach(d => {
      if (!d || typeof d !== "object") return;
      const label = cleanStr(d.label, 40);
      const items = normItems(d.items);
      if (!label || !items.length) return;
      const icon = ALLOWED_ICONS.has(d.icon) ? d.icon : "check";
      out.push({ label, icon, items });
    });
  }
  return out;
}

function normNode(n) {
  if (!n || typeof n !== "object") return null;
  const title = cleanStr(n.title, 80);
  if (!title) return null;
  return {
    type: ALLOWED_NODE_TYPES.has(n.type) ? n.type : "academic",
    title,
    time: cleanStr(n.time, 40),
    summary: cleanStr(n.summary, 500),
    detail: normDetail(n.detail),
  };
}

function normPath(p) {
  if (!p || typeof p !== "object") return null;
  const title = cleanStr(p.title, 80);
  if (!title) return null;
  const nodes = (Array.isArray(p.nodes) ? p.nodes : []).slice(0, 9).map(normNode).filter(Boolean);
  if (nodes.length < 3) return null;
  const meta = p.meta && typeof p.meta === "object" ? p.meta : {};
  return {
    title,
    tag: cleanStr(p.tag, 20) || null,
    intro: cleanStr(p.intro, 500),
    meta: {
      difficulty: cleanStr(meta.difficulty, 10, "中"),
      duration: cleanStr(meta.duration, 30, "待定"),
      cost: cleanStr(meta.cost, 40, "待定"),
      success: cleanStr(meta.success, 10, "中"),
      suitable: cleanStr(meta.suitable, 80, "根据个人情况判断"),
    },
    nodes,
  };
}

function defaultPlanB(inp) {
  if ((inp.position || inp.company)) {
    return [
      { title: "直投受阻", text: "先进入同行业次一级公司积累 1–2 年，再通过社招跳槽进入目标公司。" },
      { title: "面试被挂", text: "复盘每轮面试，针对薄弱环节专项训练，寻找内推或隔段时间再投。" },
      { title: "简历关过不了", text: "补充相关实习 / 项目经历，找内推绕过简历池，或先投相邻岗位积累经验。" },
    ];
  }
  return [
    { title: "申请被拒", text: "转投同国梯队院校或换国家，部分项目有春季入学；也可先工作 / 深造一年再申请。" },
    { title: "语言不达标", text: "选择语言要求更匹配的项目，或走语言班 / 预科等桥梁路径，部分学校接受有条件录取。" },
    { title: "预算超支", text: "优先考虑性价比更高的地区，或申请奖学金、助研 / 助教岗位。" },
  ];
}

function extractPlan(text, inp) {
  const obj = parseJson(text);
  if (!obj || typeof obj !== "object") throw new Error("AI 返回的格式无法识别，请点击重试");
  const start = obj.start && typeof obj.start === "object" ? obj.start : {};
  const goal = obj.goal && typeof obj.goal === "object" ? obj.goal : {};
  const paths = (Array.isArray(obj.paths) ? obj.paths : []).slice(0, 4).map(normPath).filter(Boolean);
  if (paths.length < 3) throw new Error("AI 返回的路径不足 3 条，请点击重试");
  const planB = [];
  if (Array.isArray(obj.planB)) {
    obj.planB.slice(0, 6).forEach(b => {
      if (b && typeof b === "object") {
        const t = cleanStr(b.title, 40);
        const x = cleanStr(b.text, 300);
        if (t && x) planB.push({ title: t, text: x });
      }
    });
  }
  if (!paths.some(p => p.tag)) paths[0].tag = "推荐";
  const plan = {
    start: { title: "起点", summary: cleanStr(start.summary, 200) || "当前状况" },
    goal: { title: "目标", summary: cleanStr(goal.summary, 200) || "目标" },
    paths,
    planB: planB.length ? planB : defaultPlanB(inp),
  };
  enrichPlanKb(plan, inp);
  return plan;
}

function kbUniBlock(uni) {
  const pairs = [
    ["排名定位", [uni.rank, uni.focus].filter(Boolean).join(" · ")],
    ["雅思要求", uni.ielts], ["托福要求", uni.toefl], ["GPA 参考", uni.gpa],
    ["GRE / GMAT", uni.gre], ["费用参考", uni.fee], ["申请时间线", uni.timeline], ["备注", uni.notes],
  ];
  const items = [];
  pairs.forEach(([k, v]) => {
    if (v) items.push({ t: (k + "：" + v), key: k === "排名定位" || k === "GPA 参考" });
  });
  if (uni.source) items.push({ t: "来源：" + uni.source, key: false });
  return { label: "知识库 · 院校信息（2026-08）", icon: "grad", items: items.slice(0, 10) };
}

function kbJobBlock(job) {
  const items = [{ t: job.name + (job.en ? "（" + job.en + "）" : ""), key: true }];
  if (job.companies && job.companies.length) items.push({ t: "常见雇主：" + job.companies.join(" / "), key: false });
  if (job.edu) items.push({ t: "学历参考：" + job.edu, key: false });
  (job.skills || []).slice(0, 6).forEach(s => items.push({ t: String(s), key: true }));
  return { label: "知识库 · 岗位信息（2026-08）", icon: "briefcase", items: items.slice(0, 10) };
}

function kbUnisBlock(unis) {
  const items = [];
  unis.slice(0, 3).forEach(u => {
    if (!u || !u.name) return;
    const gpa = String(u.gpa || "").split("，")[0];
    items.push({ t: u.name + (gpa ? "：GPA " + gpa : "") + "；雅思 " + (u.ielts || ""), key: true });
  });
  items.push({ t: "以上为本地知识库中的适配院校候选，请以官网最新信息为准", key: false });
  return { label: "知识库 · 适配院校（2026-08）", icon: "grad", items: items.slice(0, 6) };
}

function hasKbBlock(node, prefix) {
  return (node.detail || []).some(d => String(d.label || "").startsWith(prefix));
}

function uniMatchKeys(uni) {
  const keys = [];
  if (uni.name) {
    keys.push(uni.name);
    const short = uni.name.replace("大学", "").replace("理工学院", "").replace("学院", "");
    if (short && short !== uni.name) keys.push(short);
  }
  if (uni.en) {
    keys.push(uni.en);
    const first = uni.en.split(" ")[0];
    if (first && first.length >= 2) keys.push(first);
  }
  return keys.filter(Boolean);
}

function enrichPlanKb(plan, inp) {
  const kb = inp.kb;
  if (!kb || typeof kb !== "object") return plan;
  const uni = kb.university;
  const job = kb.job;
  const note = kb.companyNote;
  const unis = kb.recommendedUnis;
  const company = String(inp.company || "");
  (plan.paths || []).forEach(path => {
    let uniDone = false, jobDone = false, noteDone = false;
    (path.nodes || []).forEach(node => {
      const title = (node.title || "") + " " + (node.summary || "");
      const ntype = node.type;
      if (!uniDone && uni && uni.name && !hasKbBlock(node, "知识库 · 院校信息") &&
          uniMatchKeys(uni).some(k => title.indexOf(k) >= 0)) {
        node.detail.push(kbUniBlock(uni));
        uniDone = true;
      }
      if (!jobDone && job && job.name && !hasKbBlock(node, "知识库 · 岗位信息") &&
          (["skill", "work", "interview", "offer", "goal"].indexOf(ntype) >= 0 || title.indexOf(job.name) >= 0)) {
        node.detail.push(kbJobBlock(job));
        jobDone = true;
      }
      if (!noteDone && note && !hasKbBlock(node, "知识库 · 公司时间线") &&
          (ntype === "application" || (company && title.indexOf(company) >= 0))) {
        node.detail.push({ label: "知识库 · 公司时间线（2026-08）", icon: "time", items: [{ t: String(note), key: true }] });
        noteDone = true;
      }
    });
    if (uni && uni.name && !uniDone) {
      const node = (path.nodes || []).find(n => ["application", "admit", "grad"].indexOf(n.type) >= 0);
      if (node) node.detail.push(kbUniBlock(uni));
    }
    if (Array.isArray(unis) && unis.length) {
      const node = (path.nodes || []).find(n =>
        !hasKbBlock(n, "知识库 · 适配院校") &&
        ((n.title || "").indexOf("适配院校") >= 0 || (n.title || "").indexOf("院校建议") >= 0)
      );
      if (node) node.detail.push(kbUnisBlock(unis));
    }
  });
  return plan;
}

async function callDeepSeek(inp, env) {
  const payload = {
    model: String(env.AI_MODEL || DEFAULT_MODEL),
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      { role: "user", content: buildPrompt(inp) },
    ],
  };
  let lastData = null;
  for (let attempt = 1; attempt <= 2; attempt++) {
    let res;
    try {
      res = await fetch(API_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer " + env.DEEPSEEK_API_KEY,
        },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      throw new Error("无法连接 AI 服务，请检查网络后重试（" + String(e.message || e).slice(0, 200) + "）");
    }
    if (!res.ok) {
      let detail = "";
      try {
        const err = await res.json();
        detail = (err.error && err.error.message) || err.message || "";
      } catch (e) { /* 忽略 */ }
      if (res.status === 401) throw new Error("DeepSeek 密钥无效或已过期，请检查环境变量 DEEPSEEK_API_KEY（应以 sk- 开头，不要有多余空格）");
      if (res.status === 402) throw new Error("DeepSeek 账户余额不足，请前往 DeepSeek 开放平台充值后重试");
      if (res.status === 429) throw new Error("AI 服务请求过于频繁或额度不足，请稍后再试");
      throw new Error("AI 服务返回错误：" + (detail || ("HTTP " + res.status)).slice(0, 300));
    }
    let data;
    try {
      data = await res.json();
    } catch (e) {
      throw new Error("AI 服务响应异常，请重试");
    }
    const text = extractChatText(data);
    if (text) return extractPlan(text, inp);
    lastData = data;
  }
  const choices = lastData && Array.isArray(lastData.choices) ? lastData.choices : [];
  const msg = choices[0] && choices[0].message ? choices[0].message : {};
  throw new Error(
    "AI 返回内容为空，请重试（finish_reason=" + (choices[0] && choices[0].finish_reason) +
    ", content_len=" + String(msg.content || "").length +
    ", reasoning_len=" + String(msg.reasoning_content || "").length + "）"
  );
}

function clientIp(request) {
  return (
    request.headers.get("CF-Connecting-IP") ||
    request.headers.get("EO-Client-IP") ||
    (request.headers.get("X-Forwarded-For") || "").split(",")[0].trim() ||
    "unknown"
  );
}

function allowRequest(ip, env) {
  const rateLimit = parseInt(env.RATE_LIMIT || "10", 10) || 10;
  const dailyQuota = parseInt(env.DAILY_QUOTA || "200", 10) || 200;
  const now = Date.now();
  const windowMs = 60 * 60 * 1000;
  const hit = hits.get(ip);
  if (!hit || now - hit.start > windowMs) {
    hits.set(ip, { start: now, count: 1 });
  } else {
    hit.count += 1;
    if (hit.count > rateLimit) {
      return { ok: false, message: "请求过于频繁，请 1 小时后再试（演示站限流）" };
    }
  }
  if (hits.size > 10000) hits.clear();
  const today = new Date().toISOString().slice(0, 10);
  if (daily.date !== today) {
    daily.date = today;
    daily.count = 0;
  }
  daily.count += 1;
  if (daily.count > dailyQuota) {
    return { ok: false, message: "今日演示额度已用完，请明天再来（每日限额 " + dailyQuota + " 次）" };
  }
  return { ok: true };
}

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, X-Demo-Code",
    },
  });
}

export async function onRequestPost({ request, env }) {
  const code = String(env.DEMO_ACCESS_CODE || "").trim();
  if (code && request.headers.get("X-Demo-Code") !== code) {
    return jsonResponse({ ok: false, error: "演示访问码不正确，请与站点管理员联系" }, 403);
  }
  const limit = allowRequest(clientIp(request), env);
  if (!limit.ok) return jsonResponse({ ok: false, error: limit.message }, 429);
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonResponse({ ok: false, error: "请求格式有误" }, 400);
  }
  const inp = body && typeof body.input === "object" ? body.input : null;
  if (!inp) return jsonResponse({ ok: false, error: "请求格式有误" }, 400);
  if (body && typeof body.kb === "object") inp.kb = body.kb;
  const key = String(env.DEEPSEEK_API_KEY || "").trim();
  if (!key) {
    return jsonResponse({ ok: false, error: "尚未配置 AI 密钥：请在 EdgeOne Pages 项目设置中添加 DEEPSEEK_API_KEY 环境变量" }, 503);
  }
  try {
    const plan = await callDeepSeek(inp, env);
    return jsonResponse({ ok: true, plan, model: String(env.AI_MODEL || DEFAULT_MODEL) });
  } catch (e) {
    return jsonResponse({ ok: false, error: String(e.message || "AI 生成失败，请重试") }, 502);
  }
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-Demo-Code",
  }});
}
