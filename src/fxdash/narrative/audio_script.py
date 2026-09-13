"""Spoken copy derived from a frozen edition, with no new model call or forecast."""
from __future__ import annotations

import math
from datetime import date, datetime

from . import morning as M, briefing_archive as A

VERSION = "audio-v1"
PAIRS = {
    "USDAUD": ("the Australian dollar", "澳元"), "USDCAD": ("the Canadian dollar", "加元"),
    "USDEUR": ("the euro", "欧元"), "USDJPY": ("the Japanese yen", "日元"),
    "USDMXN": ("the Mexican peso", "墨西哥比索"), "USDNOK": ("the Norwegian krone", "挪威克朗"),
}
FACTORS = {
    "DOLLAR_LOO": ("the dollar basket excluding this pair", "排除本货币对的美元篮子"),
    "CARRY_LOO": ("the carry basket excluding this pair", "排除本货币对的利差交易篮子"),
    "d2Y_DIFF": ("the change in the two year yield differential", "两年期利差变化"),
    "d10Y_DIFF": ("the change in the ten year yield differential", "十年期利差变化"),
    "dVIX": ("the change in equity implied volatility", "股票隐含波动率变化"),
    "WTI": ("West Texas Intermediate oil", "西得克萨斯原油"),
    "BRENT": ("Brent oil", "布伦特原油"), "COPPER": ("copper", "铜价"),
    "GOLD": ("gold", "金价"), "dHY_OAS": ("the change in high yield credit spreads", "高收益信用利差变化"),
    "dBAA": ("the change in investment grade credit spreads", "投资级信用利差变化"),
    "EMB": ("emerging market bonds", "新兴市场债券"),
}


def spoken_date(value, lang):
    d = date.fromisoformat(value)
    return f"{d.year}年{d.month}月{d.day}日" if lang == "zh" else f"{d:%B} {d.day}, {d.year}"


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("non_finite_audio_input")
    return value


def signed_bp(value, lang):
    value = round(number(value), 1)
    sign = ("正" if value > 0 else "负" if value < 0 else "") if lang == "zh" else (
        "plus " if value > 0 else "minus " if value < 0 else "")
    return f"{sign}{abs(value):.1f}" + ("个基点" if lang == "zh" else " basis points")


def compose(edition, packet, lang):
    """Recheck the saved links. An old archive is never upgraded with live news."""
    if lang not in {"en", "zh"}:
        raise ValueError("unsupported_audio_language")
    if (not A.valid_edition(edition, edition.get("date"), mode=edition.get("mode"))
            or edition["state"] not in {"ready", "numbers_only"}
            or edition.get("packet_hash") != M.digest(packet)
            or M.digest(edition.get("evidence")) != M.digest(packet)
            or edition["attribution_as_of"] != packet["as_of"]):
        raise ValueError("audio_evidence_mismatch")
    rows = packet["pairs"]
    if len(rows) != 6 or {r["pair"] for r in rows} != set(PAIRS):
        raise ValueError("audio_needs_six_pairs")
    for r in rows:
        if r["date"] != packet["as_of"]:
            raise ValueError("audio_input_dates_disagree")
        number(r["y"])
        number(r["residual"])
        for f in r.get("leading", []):
            number(f["contribution_bp"])
    generated = datetime.fromisoformat(edition["generated_at"])
    fetched = datetime.fromisoformat(packet["fetched_at"])
    if not generated.tzinfo or not fetched.tzinfo or fetched > generated:
        raise ValueError("invalid_audio_source_time")
    if (M.local_time(generated).date().isoformat() != edition['date']
            or packet['as_of'] > edition['date']):
        raise ValueError("invalid_audio_edition_date")
    zh = lang == "zh"
    lines = [
        (f"这是{spoken_date(edition['date'], lang)}的外汇研究简报，采用合成语音。"
         f"归因数据截至{spoken_date(packet['as_of'], lang)}。下面回顾六个货币对中，单日波动绝对值最大的三个。"
         "所有报价都以美元为基准，正收益表示美元走强。数字采用对数收益基点，一百个基点的对数收益约等于百分之一的价格变化。")
        if zh else
        (f"This is the FX research briefing dated {spoken_date(edition['date'], lang)}, read by a synthetic voice. "
         f"Attribution is through {spoken_date(packet['as_of'], lang)}. We review the three largest absolute daily moves among six currency pairs. "
         "All quotes have the dollar as the base currency. Positive returns mean a stronger dollar. "
         "Figures are log return basis points; one hundred basis points is approximately a one percent price change.")
    ]
    for r in sorted(rows, key=lambda r: (-abs(r["y"]), r["pair"]))[:3]:
        currency = PAIRS[r["pair"]][int(zh)]
        leading = r.get("leading", [])
        line = (f"美元兑{currency}，单日收益为{signed_bp(r['y']*1e4, lang)}。"
                if zh else f"Against {currency}, the dollar's daily return was {signed_bp(r['y']*1e4, lang)}. ")
        if r.get("provisional"):
            line += "这组数字仍待确认。" if zh else "These figures are provisional. "
        if leading:
            f = max(leading, key=lambda f: abs(f["contribution_bp"]))
            label = FACTORS.get(f["factor"], (f["factor"], f["factor"]))[int(zh)]
            line += (f"贡献绝对值最大的是{label}，贡献为{signed_bp(f['contribution_bp'], lang)}。"
                     if zh else f"The largest absolute factor contribution was {label}, at {signed_bp(f['contribution_bp'], lang)}. ")
        line += (f"模型未解释的残差为{signed_bp(r['residual']*1e4, lang)}。"
                 if zh else f"The unexplained residual was {signed_bp(r['residual']*1e4, lang)}.")
        lines.append(line)
    # Re-run the existing source/wording validator on the archived note. It is a
    # screening check, not a claim that the underlying reporting is true.
    from .driver_notes import validate, source_set
    selected = []
    by_pair = {r["pair"]: r for r in rows}
    for item in edition["notes"]:
        row = by_pair.get(item.get("pair"))
        raw = item.get("note")
        if (row and raw and not validate(raw, row, source_set(packet, row), packet["fetched_at"])
                and raw["assessment"] != "insufficient_evidence"):
            event = raw[lang]["event"]
            if len(event) <= (180 if zh else 700):
                selected.append(event)
                break
    if selected:
        lines.append(("保存的新闻解读中，有一条供进一步核对。" if zh else
                      "One saved news interpretation is included for further checking. ") + selected[0])
    else:
        lines.append("本段没有纳入可用的新闻解读，保留数字复盘。" if zh else
                     "No usable news interpretation is included in this audio; the numerical review remains available.")
    lines.append(
        "接下来关注三件事：核对所引报道的事件日期与独立来源；检查主要因子的后续变化是否与已保存的敏感度一致；"
        "留意残差是否持续偏大。这些是研究核验事项，本稿未接入经济日历，不能据此推断今天有哪些定时发布。"
        "因子贡献反映模型中的同期关联，不能证明因果关系。数字和新闻均对应本期保存时点，历史播报不代表最新行情。"
        "完整文字稿和来源链接可在网页查看。本简报不提供交易指令。"
        if zh else
        "Next, check the cited event dates and independent sources. Compare subsequent factor moves with the saved sensitivities, "
        "and watch for persistently large residuals. These are research checks. This script has no economic calendar and does not list today's scheduled releases. "
        "Factor contributions describe contemporaneous associations, not proven causes. Figures and news belong to this saved edition; "
        "an archived recording does not represent current markets. The transcript and source links are on the website. This briefing provides no trading instructions."
    )
    return "\n\n".join(lines)
