#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
转录文本分析脚本（纯 Python，无需外部 API）
===========================================
使用 jieba 中文分词对转录文本进行：
  - 关键词提取（TextRank 算法）
  - 核心观点句提取（关键词密度评分）
  - 数据与事实提取（正则匹配数字/品牌）
  - 茶饮创业者参考价值评分（规则打分）
  - 适合转发亮点提炼

依赖：jieba（pip install jieba）
"""

import re
import time
from collections import Counter
from pathlib import Path

try:
    import jieba
    import jieba.analyse
    jieba.setLogLevel("WARNING")   # 关闭 jieba 加载日志
except ImportError:
    raise SystemExit("❌ 请先安装 jieba：pip install jieba")

# ──────────────────────────────────────────────
# 常量配置
# ──────────────────────────────────────────────

DOWNLOADS_DIR = Path(__file__).parent / "downloads"

# 茶饮行业高价值关键词（用于评分）
TEA_DOMAIN_KEYWORDS = {
    "奶茶", "茶饮", "开店", "品牌", "位置", "选址", "加盟", "创业",
    "门店", "营业额", "利润", "成本", "复购", "蜜雪", "瑞幸", "古茗",
    "斩杀线", "倒闭", "新品", "研发", "外卖", "加盟商", "快招",
}

# 可操作建议信号词
ACTIONABLE_SIGNALS = [
    "一定要", "千万", "建议", "关键", "最重要", "核心", "注意",
    "怎么", "如何", "方法", "策略", "步骤", "第一", "第二", "第三",
]

# 风险提示信号词
RISK_SIGNALS = [
    "倒闭", "亏损", "失败", "风险", "陷阱", "骗", "死", "不挣钱",
    "必死", "慎重", "不要", "千万别",
]


# ──────────────────────────────────────────────
# 文本预处理
# ──────────────────────────────────────────────

def load_transcript(txt_path: Path) -> str:
    """读取转录文件，跳过文件头注释，返回纯正文"""
    raw = txt_path.read_text(encoding="utf-8")
    lines = []
    header_done = False
    for line in raw.splitlines():
        if not header_done:
            if line.startswith("#") or line.startswith("─") or line.strip() == "":
                continue
            header_done = True
        lines.append(line)
    return "\n".join(lines).strip()


def split_sentences(text: str) -> list[str]:
    """
    切分句子，兼容两种情况：
    1. 有标点（。！？；）→ 按标点切分
    2. 无标点（语音转录常见）→ 按转折/连接词切分，再按固定窗口补充
    """
    # 优先按标点切分
    parts = re.split(r"[。！？；…]+", text)
    parts = [s.strip() for s in parts if len(s.strip()) >= 12]
    if len(parts) >= 4:
        return parts

    # 无标点：按语义连接词切分
    # 在这些词前插入换行符再按行切分
    BREAK_WORDS = (
        r"(所以|然后|但是|因为|如果|那么|同时|首先|其次|最后|另外|"
        r"不过|而且|况且|不仅|虽然|尽管|总之|综上|总的来说|"
        r"第一个|第二个|第三个|还有一个|回过头来)"
    )
    marked = re.sub(BREAK_WORDS, r"\n\1", text)
    parts = [s.strip() for s in marked.splitlines() if len(s.strip()) >= 12]
    if len(parts) >= 4:
        return parts

    # 兜底：按固定字数滑动切分（每 60 字一段，重叠 10 字）
    result = []
    step = 60
    for i in range(0, len(text), step):
        chunk = text[i:i + step + 10].strip()
        if len(chunk) >= 12:
            result.append(chunk)
    return result


# ──────────────────────────────────────────────
# 关键词提取
# ──────────────────────────────────────────────

def extract_keywords(text: str, top_n: int = 15) -> list[tuple[str, float]]:
    """
    用 jieba TextRank 算法提取关键词。
    返回 [(词, 权重), ...] 按权重降序排列。
    """
    keywords = jieba.analyse.textrank(text, topK=top_n, withWeight=True,
                                       allowPOS=("ns", "n", "vn", "v", "nr"))
    return keywords


# ──────────────────────────────────────────────
# 核心观点句提取
# ──────────────────────────────────────────────

def extract_key_sentences(text: str, keywords: list[tuple[str, float]],
                           top_n: int = 6) -> list[str]:
    """
    按关键词密度对每个句子打分，返回得分最高的 top_n 句。

    打分规则：
    - 句子包含关键词 → 加上该关键词权重
    - 句子含有数字   → 额外 +0.3（数据佐证更有说服力）
    - 句子长度适中（20-60字）→ 额外 +0.2（太短无意义，太长难读）
    - 位置权重：前 20% 和后 20% 句子 +0.15（开头结论、结尾总结）
    """
    sentences = split_sentences(text)
    if not sentences:
        return []

    kw_weights = {kw: w for kw, w in keywords}
    total = len(sentences)
    scored = []

    for idx, sent in enumerate(sentences):
        score = 0.0
        # 关键词权重累加
        for kw, weight in kw_weights.items():
            if kw in sent:
                score += weight
        # 数字加分
        if re.search(r"\d+", sent):
            score += 0.3
        # 长度加分
        if 20 <= len(sent) <= 80:
            score += 0.2
        # 位置加分
        if idx < total * 0.2 or idx > total * 0.8:
            score += 0.15
        scored.append((score, sent))

    # 按分数降序，去重（避免内容过于相似的句子）
    scored.sort(key=lambda x: -x[0])
    selected = []
    seen_start = set()
    for score, sent in scored:
        prefix = sent[:8]   # 用前8字判断相似度
        if prefix not in seen_start:
            selected.append(sent)
            seen_start.add(prefix)
        if len(selected) >= top_n:
            break
    return selected


# ──────────────────────────────────────────────
# 数据与事实提取
# ──────────────────────────────────────────────

def extract_data_facts(text: str) -> list[str]:
    """
    提取含有具体数字/比例/品牌名的句子，
    这些句子通常包含最有价值的事实依据。
    """
    # 品牌名词
    brands = ["蜜雪", "瑞幸", "古茗", "喜茶", "奈雪", "茶颜", "霸王茶姬"]
    sentences = split_sentences(text)
    result = []

    for sent in sentences:
        has_number = bool(re.search(r"\d+[%万千亿元家天个条]|\d{2,}", sent))
        has_brand  = any(b in sent for b in brands)
        if has_number or has_brand:
            result.append(sent)

    # 按句子长度排序（较长的往往信息更完整）
    result.sort(key=len, reverse=True)
    return result[:8]


# ──────────────────────────────────────────────
# 茶饮创业者参考价值评分
# ──────────────────────────────────────────────

def score_for_tea_entrepreneur(text: str, keywords: list[tuple[str, float]]) -> tuple[int, list[str]]:
    """
    规则打分：对茶饮创业者的参考价值（满分 10 分）。

    五个维度各 2 分：
    ① 行业相关性  ② 数据密度  ③ 可操作建议  ④ 风险提示  ⑤ 内容深度
    """
    reasons = []
    total   = 0

    # ① 行业相关性（命中领域关键词数量）
    domain_hits = sum(1 for kw in TEA_DOMAIN_KEYWORDS if kw in text)
    if domain_hits >= 8:
        total += 2; reasons.append(f"① 行业相关性极高（含 {domain_hits} 个核心行业词）")
    elif domain_hits >= 4:
        total += 1; reasons.append(f"① 行业相关性较高（含 {domain_hits} 个核心行业词）")
    else:
        reasons.append(f"① 行业相关性一般（含 {domain_hits} 个核心行业词）")

    # ② 数据密度（具体数字/比例出现次数）
    data_matches = re.findall(r"\d+[%万千亿元家天个]|\d{2,}", text)
    if len(data_matches) >= 10:
        total += 2; reasons.append(f"② 数据密度高（含 {len(data_matches)} 处具体数字）")
    elif len(data_matches) >= 5:
        total += 1; reasons.append(f"② 数据密度中等（含 {len(data_matches)} 处具体数字）")
    else:
        reasons.append(f"② 数据较少（仅 {len(data_matches)} 处数字）")

    # ③ 可操作建议
    action_hits = sum(1 for s in ACTIONABLE_SIGNALS if s in text)
    if action_hits >= 6:
        total += 2; reasons.append(f"③ 操作建议丰富（含 {action_hits} 个指导性表述）")
    elif action_hits >= 3:
        total += 1; reasons.append(f"③ 操作建议适中（含 {action_hits} 个指导性表述）")
    else:
        reasons.append(f"③ 操作建议较少")

    # ④ 风险提示
    risk_hits = sum(1 for s in RISK_SIGNALS if s in text)
    if risk_hits >= 4:
        total += 2; reasons.append(f"④ 风险提示充分（含 {risk_hits} 处警示内容）")
    elif risk_hits >= 2:
        total += 1; reasons.append(f"④ 有一定风险提示（含 {risk_hits} 处）")
    else:
        reasons.append(f"④ 风险提示较少")

    # ⑤ 内容深度（字数 + 关键词权重总和）
    kw_weight_sum = sum(w for _, w in keywords)
    if len(text) >= 1000 and kw_weight_sum >= 2.0:
        total += 2; reasons.append(f"⑤ 内容深度好（{len(text)} 字，信息密度高）")
    elif len(text) >= 500:
        total += 1; reasons.append(f"⑤ 内容适中（{len(text)} 字）")
    else:
        reasons.append(f"⑤ 内容较短（{len(text)} 字）")

    return total, reasons


# ──────────────────────────────────────────────
# 适合转发的亮点句
# ──────────────────────────────────────────────

def extract_shareable_highlights(text: str, keywords: list[tuple[str, float]]) -> list[str]:
    """
    提炼适合转发的亮点内容。

    策略：在冲击性词汇周围截取一段上下文（20-50字），
    确保内容完整、可读，适合截图转发。
    """
    impact_words = [
        "必死无疑", "选址决定生死", "品牌斩杀线", "慢性死亡", "猝死",
        "逆天改命", "90%", "千万不要", "必须得吃苦", "10%",
    ]
    highlights = []
    seen = set()

    for word in impact_words:
        idx = text.find(word)
        if idx == -1:
            continue
        # 向前取 10 字，向后取 40 字
        start = max(0, idx - 10)
        end   = min(len(text), idx + 50)
        snippet = text[start:end].strip()
        # 去重（避免相邻词产生重叠片段）
        key = snippet[:15]
        if key not in seen and len(snippet) >= 15:
            highlights.append(snippet)
            seen.add(key)

    return highlights[:5]


# ──────────────────────────────────────────────
# 报告生成
# ──────────────────────────────────────────────

def generate_report(txt_path: Path, transcript: str) -> str:
    """
    综合所有分析结果，生成结构化文本报告。
    """
    source_name = txt_path.stem.replace("_", " ")
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    # ── 运行各项分析 ──
    keywords     = extract_keywords(transcript, top_n=15)
    key_sents    = extract_key_sentences(transcript, keywords, top_n=6)
    data_facts   = extract_data_facts(transcript)
    score, score_reasons = score_for_tea_entrepreneur(transcript, keywords)
    highlights   = extract_shareable_highlights(transcript, keywords)

    # 词频统计（用于补充展示）
    words        = jieba.lcut(transcript)
    word_freq    = Counter(w for w in words if len(w) >= 2)
    top_words    = word_freq.most_common(20)

    # ── 拼装报告 ──
    sep  = "═" * 54
    sep2 = "─" * 54

    lines = [
        sep,
        "  📊  抖音视频内容分析报告",
        sep,
        f"  来源文件：{source_name}",
        f"  分析时间：{now}",
        f"  原文字数：{len(transcript):,} 字  |  句子数：{len(split_sentences(transcript))}",
        sep,
        "",
        "【一、高频词 TOP 20】（词频统计）",
        sep2,
    ]
    # 每行显示 4 个词
    freq_chunks = [top_words[i:i+4] for i in range(0, len(top_words), 4)]
    for chunk in freq_chunks:
        row = "  ".join(f"{w}({c}次)" for w, c in chunk)
        lines.append(f"  {row}")

    lines += [
        "",
        "【二、关键词 TOP 15】（TextRank 权重）",
        sep2,
    ]
    kw_chunks = [keywords[i:i+3] for i in range(0, len(keywords), 3)]
    for chunk in kw_chunks:
        row = "   ".join(f"{kw}({w:.3f})" for kw, w in chunk)
        lines.append(f"  {row}")

    lines += [
        "",
        "【三、核心观点（关键句提取）】",
        sep2,
    ]
    for i, sent in enumerate(key_sents, 1):
        lines.append(f"  {i}. {sent}。")

    lines += [
        "",
        "【四、数据与事实】",
        sep2,
    ]
    if data_facts:
        for i, sent in enumerate(data_facts, 1):
            lines.append(f"  {i}. {sent}。")
    else:
        lines.append("  （未提取到明显数据句）")

    lines += [
        "",
        f"【五、对茶饮创业者的参考价值】  评分：{score}/10",
        sep2,
    ]
    for r in score_reasons:
        lines.append(f"  {r}")
    # 分值解读
    if score >= 8:
        verdict = "强烈推荐，内容质量高，有很强的实操指导意义"
    elif score >= 6:
        verdict = "值得参考，包含有效信息，建议结合实际情况判断"
    elif score >= 4:
        verdict = "一般参考，部分内容有价值，需自行甄别"
    else:
        verdict = "参考价值有限，内容较为笼统"
    lines.append(f"  综合评价：{verdict}")

    lines += [
        "",
        "【六、适合转发的亮点内容】",
        sep2,
    ]
    if highlights:
        for i, h in enumerate(highlights, 1):
            lines.append(f"  💬 {i}. 「{h}」")
    else:
        lines.append("  （未找到符合条件的短句，建议从核心观点中手动摘选）")

    lines += ["", sep]
    return "\n".join(lines)


# ──────────────────────────────────────────────
# 主函数（可独立运行，也可被 main.py 调用）
# ──────────────────────────────────────────────

def analyze_file(txt_path: Path) -> Path | None:
    """
    分析单个转录 txt 文件，生成 _analysis.txt。
    :return: 生成的分析文件路径，失败返回 None
    """
    transcript = load_transcript(txt_path)
    if not transcript:
        print(f"  ⚠️  文件为空，跳过: {txt_path.name}")
        return None

    print(f"  📖 正在分析：{txt_path.name}（{len(transcript):,} 字）")
    report = generate_report(txt_path, transcript)

    out_path = txt_path.with_name(txt_path.stem + "_analysis.txt")
    out_path.write_text(report, encoding="utf-8")
    print(f"  💾 已保存：{out_path.name}")
    return out_path


def main():
    import sys
    print("=" * 54)
    print("  📊  转录文本分析（纯 Python · jieba）")
    print("=" * 54)

    if len(sys.argv) > 1:
        txt_files = [Path(p) for p in sys.argv[1:]
                     if Path(p).suffix == ".txt" and "_analysis" not in Path(p).stem]
    else:
        if not DOWNLOADS_DIR.exists():
            print(f"❌ 目录不存在：{DOWNLOADS_DIR}")
            sys.exit(1)
        txt_files = [f for f in sorted(DOWNLOADS_DIR.glob("*.txt"))
                     if "_analysis" not in f.stem and "_summary" not in f.stem]

    if not txt_files:
        print("❌ 未找到待分析的转录文件")
        sys.exit(1)

    pending = [f for f in txt_files
               if not f.with_name(f.stem + "_analysis.txt").exists()]
    skipped = [f for f in txt_files if f not in pending]

    if skipped:
        print(f"\n⏭  已有分析结果，跳过 {len(skipped)} 个文件")

    if not pending:
        print("✅ 所有文件均已分析完成")
        return

    print(f"\n📋 待分析：{len(pending)} 个文件\n")
    success = 0
    for f in pending:
        out = analyze_file(f)
        if out:
            success += 1

    print(f"\n{'=' * 54}")
    print(f"🎉  完成！成功分析 {success}/{len(pending)} 个文件")
    print(f"📁  结果保存在：{DOWNLOADS_DIR.resolve()}/")
    print("=" * 54)


if __name__ == "__main__":
    main()
