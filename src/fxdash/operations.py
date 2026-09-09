"""Local, offline-readable operations reports built only from saved artifacts."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import uuid
from zoneinfo import ZoneInfo

from .data.vintage_audit import audit_archive
from .narrative import morning as M
from .narrative.acceptance import assess
from .narrative.morning_health import latest_observation


CHECK_LABELS = {
    "dispatch_records_readable": "调用记录完整且身份一致",
    "scheduled_preparation": "定时入口在 08:50 至 09:00 完成采集",
    "scheduled_publication": "定时入口在 09:00 至 09:05 发起并完成发布",
    "ordered_dispatch_chain": "采集结束后才开始发布",
    "eligible_pre_cutoff_inputs": "输入满足日期与 09:00 截止要求",
    "all_six_pairs": "六个货币对齐全",
    "readable_edition": "冻结晨报可读",
    "packet_hash_matches": "晨报引用的输入与原始包一致",
    "quant_input_archive_verified": "量化输入已留档且校验通过",
    "edition_on_time": "冻结稿在 09:00 至 09:02 生成",
    "matching_push": "推送回执与冻结稿哈希一致",
    "push_within_five_minutes": "推送在 09:05 前完成",
}


def collect_report(output_dir, *, start_date=None, clock=M.now_utc, archive_limit=20):
    observed = clock()
    M.local_time(observed)  # Reject ambiguous local timestamps.
    enrollment = M.read_json(Path(output_dir) / "briefing" / "acceptance.json")
    start = start_date or enrollment.get("start_date")
    required = enrollment.get("required_consecutive_weekdays", 5)
    acceptance = {"state": "not_enrolled", "start_date": None, "days": [],
                  "required_consecutive_weekdays": 5, "consecutive_passes": 0, "event_context_days": 0}
    if start:
        if type(required) is not int or required < 1:
            raise ValueError("invalid_acceptance_enrollment")
        acceptance = assess(output_dir, start_date=start, required_days=required, clock=lambda: observed)
    return {"schema_version": 1, "observed_at": observed.isoformat(),
            "acceptance": acceptance, "archive": audit_archive(output_dir, limit=archive_limit),
            "clock_observation": latest_observation(output_dir, clock=lambda: observed),
            "scope": "Local saved artifacts only. No fetching, model fitting, generation or publication."}


def _text(value):
    return escape(str(value)) if value is not None else "未记录"


def _table(headers, rows):
    head = "".join(f'<th scope="col">{_text(c)}</th>' for c in headers)
    body = "".join("<tr>" + "".join(f"<td>{_text(c)}</td>" for c in row) + "</tr>" for row in rows)
    return f'<div class="table-scroll" tabindex="0"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _font_styles():
    root = Path(__file__).parent / "web" / "static" / "fonts"
    styles = []
    for family, file, weight in (("Outfit", "outfit-latin.woff2", "100 900"),
                                 ("IBM Plex Mono", "ibm-plex-mono-latin-400.woff2", "400")):
        encoded = base64.b64encode((root / file).read_bytes()).decode("ascii")
        styles.append(f"@font-face{{font-family:'{family}';font-weight:{weight};font-style:normal;"
                      f"font-display:swap;src:url(data:font/woff2;base64,{encoded}) format('woff2')}}")
    licenses = "\n\n".join((root / name).read_text(encoding="utf-8")
                           for name in ("OFL-Outfit.txt", "OFL-IBM-Plex-Mono.txt"))
    return "\n".join(styles), escape(licenses)


def render_report(report):
    acceptance, archive = report["acceptance"], report["archive"]
    records, comparison = archive["verified_records"], archive["comparison"]
    engine_count = sum(r["kind"] == "engine_inputs" for r in records)
    baseline_count = sum(r["kind"] == "cache_baseline" for r in records)
    due_days = acceptance["days"]
    passed_days = sum(r["passed"] for r in due_days)
    labels = {"passed": "运行验收通过", "collecting": "等待首个验收日",
              "not_yet_passed": "尚未通过验收", "not_enrolled": "尚未设置验收起点"}
    status = labels[acceptance["state"]]
    tone = "good" if acceptance["state"] == "passed" else "pending"
    if archive["issues"] or comparison["state"] == "comparison_failed":
        status, tone = "留档需要检查", "pending"
    day_html = '<p class="empty">还没有到期的验收日。预览、人工试跑和测试数据都不计入正式记录。</p>'
    if due_days:
        parts = []
        for row in reversed(due_days[-20:]):
            checks = "".join(f'<li class="{"ok" if value else "fail"}">{"通过" if value else "未通过"} · '
                             f'{_text(CHECK_LABELS.get(key, key))}</li>' for key, value in row["checks"].items())
            problems = _table(("文件", "记录问题"), ((r["file"], r["reason"]) for r in row["record_issues"])) if row["record_issues"] else ""
            parts.append(f'<details class="day"><summary><span class="mono">{_text(row["date"])}</span>'
                         f'<span>{"通过" if row["passed"] else "未通过"} · '
                         f'{"含事件解读" if row["context_included"] else "无合格事件解读"}</span></summary>'
                         f'<ul class="checks">{checks}</ul>{problems}'
                         f'<p class="muted">未结束调用：{_text(row["unfinished_invocations"])}</p></details>')
        day_html = "".join(parts)
        if len(due_days) > 20:
            day_html += '<p class="muted">此处显示最近 20 个验收日，完整记录保存在同目录 report.json。</p>'
    inventory = _table(("类型", "系统观察时间 · UTC", "表数", "表内最晚日期"),
                       (("完整引擎输入" if r["kind"] == "engine_inputs" else "缓存基线",
                         datetime.fromisoformat(r["observed_at"]).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                         r["tables"], r["last_data_date"]) for r in records)) if records else '<p class="empty">还没有校验通过的输入档案。</p>'
    issues = _table(("文件", "问题", "错误类型"), ((r["file"], r["reason"], r["error_type"]) for r in archive["issues"])) if archive["issues"] else ""
    if comparison["state"] == "compared":
        rows = []
        examples = []
        for name, value in comparison["tables"].items():
            if not value["changed"]:
                continue
            if value["state"] != "compared":
                rows.append((name, "整表新增" if value["state"] == "added_table" else "整表移除", "", "", "", "", ""))
                continue
            structure = "; ".join(filter(None, (
                "新增列 " + ", ".join(value["added_columns"]) if value["added_columns"] else "",
                "移除列 " + ", ".join(value["removed_columns"]) if value["removed_columns"] else "",
                "类型变化 " + ", ".join(value["dtype_changes"]) if value["dtype_changes"] else ""))) or "无"
            rows.append((name, value["added_dates"], value["removed_dates"], value["revised_cells"],
                         value["filled_cells"], value["became_missing_cells"], structure))
            examples.extend((name, e["date"], e["column"], e["kind"], e["before"], e["after"]) for e in value["examples"])
        change_html = f'<p>最近两份同类档案中，{_text(comparison["changed_tables"])} 张表发生变化。</p>'
        change_html += f'<p class="mono small">{_text(comparison["before_observed_at"])} → {_text(comparison["after_observed_at"])}</p>'
        change_html += _table(("数据表", "新增日期", "移除日期", "数值修订", "补齐缺失", "变为缺失", "列变化"), rows) if rows else '<p class="empty">两份快照内容一致。</p>'
        if examples:
            change_html += '<details><summary>查看变化样例，每张表最多 12 个单元格</summary>' + _table(("数据表", "日期", "字段", "类型", "原值", "新值"), examples) + '</details>'
    elif comparison["state"] == "comparison_failed":
        change_html = '<p class="empty">同类档案比较失败，请检查 JSON 报告中的错误类型。没有将失败记成“数据未变”。</p>'
    else:
        change_html = '<p class="empty">还没有两份可比较的同类档案。等待后续真实运行，暂时无法判断数值是否被修订。</p>'
    observed = datetime.fromisoformat(report["observed_at"])
    gate = report.get("clock_observation", {"state": "not_recorded"})
    if gate["state"] == "not_recorded":
        clock_html = '<p class="note">尚无窗口外调度记录。缺少记录无法证明电脑在晨间是否可用。</p>'
    elif gate["state"] == "unreadable":
        clock_html = '<p class="empty">最近的窗口外调度记录无法校验，请检查原始文件。</p>'
    else:
        meaning = ("启动时已错过晨间窗口，当天没有完整晨报及匹配的推送回执。结果码为 2，留待下一工作日。"
                   if gate["state"] == "missed_window" else
                   "周末无需出刊。" if gate["phase"] == "weekend" else
                   "尚未进入晨间窗口。" if gate["phase"] == "before_window" else
                   "窗口已结束，已保存晨报与匹配的推送回执；是否按时仍以上方验收为准。")
        clock_html = (f'<p class="empty">{meaning}</p><p class="mono small">观察时间：{_text(gate["observed_at"])}</p>'
                      '<p class="note">窗口外只记录状态，不补抓新闻、不调用模型、不补造晨报、不发布。'
                      '此记录不计入正式出刊，推送回执也不能证明公网部署完成。</p>')
    fonts, licenses = _font_styles()
    values = {"FONTS": fonts, "LICENSES": licenses, "STATUS": _text(status), "TONE": tone,
              "OBSERVED": _text(report["observed_at"]),
              "LOCAL_TIME": _text(observed.astimezone(ZoneInfo(M.ZONE)).strftime("%Y-%m-%d %H:%M:%S %Z")),
              "ENGINE": str(engine_count), "BASELINE": str(baseline_count),
              "STREAK": str(acceptance["consecutive_passes"]), "REQUIRED": str(acceptance["required_consecutive_weekdays"]),
              "START": _text(acceptance["start_date"]), "DUE": str(len(due_days)), "PASSED": str(passed_days),
              "CONTEXT": str(acceptance["event_context_days"]), "DAYS": day_html, "CLOCK": clock_html, "INVENTORY": inventory,
              "ISSUES": issues, "CHANGES": change_html, "LIMIT": str(archive["inspection_limit"]),
              "CHECKED": str(archive["checked_files"]), "TOTAL": str(archive["total_capture_files"])}
    template = Path(__file__).with_name("operations_report.html").read_text(encoding="utf-8")
    # Single-pass substitution prevents artifact text from becoming a template directive.
    return re.sub(r"\{\{([A-Z_]+)\}\}", lambda match: values[match[1]], template)


def save_report(output_dir, report):
    """Append immutable snapshots; replace only the explicitly mutable latest view."""
    root = Path(output_dir).resolve()
    destination = root / "operations-acceptance" / "reports"
    if not destination.resolve().is_relative_to(root):
        raise ValueError("report_directory_outside_outputs")
    html = render_report(report)
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    stamp = datetime.fromisoformat(report["observed_at"]).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")
    snapshot = destination / (stamp + "-" + uuid.uuid4().hex[:12])
    snapshot.mkdir(parents=True, exist_ok=False)
    with (snapshot / "report.json").open("x", encoding="utf-8") as stream:
        stream.write(payload)
    with (snapshot / "index.html").open("x", encoding="utf-8") as stream:
        stream.write(html)
    temp = destination / ("latest." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            stream.write(html)
        temp.replace(destination / "latest.html")
    finally:
        temp.unlink(missing_ok=True)
    return {"snapshot": str(snapshot), "html": str(snapshot / "index.html"),
            "json": str(snapshot / "report.json"), "latest": str(destination / "latest.html")}


def main(argv=None):
    from .config import OUTPUT_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--start-date", help="otherwise read briefing/acceptance.json")
    parser.add_argument("--archive-limit", type=int, default=20)
    args = parser.parse_args(argv)
    report = collect_report(args.output_dir, start_date=args.start_date, archive_limit=args.archive_limit)
    paths = save_report(args.output_dir, report)
    print(json.dumps({**paths, "acceptance": report["acceptance"]["state"],
                      "archive": report["archive"]["state"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
