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
from .narrative.usage_acceptance import assess as assess_usage
from .narrative.morning_health import latest_observation
from .narrative.delivery_status import snapshot as delivery_snapshot, TASKS, CREDENTIALS, LANGUAGES


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
    "current_complete_inputs": "六个货币对齐全，数据日期与实际观察时间有效",
    "actual_edition_date": "稿件标注真实生成日期",
    "ordered_publication": "发布发生在生成之后，回执时间有效",
    "invocation_records_readable": "调用记录完整且身份一致",
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
    return {"schema_version": 2, "observed_at": observed.isoformat(),
            "acceptance": assess_usage(output_dir, start_date=start, clock=lambda: observed),
            "scheduled_acceptance": acceptance, "archive": audit_archive(output_dir, limit=archive_limit),
            "clock_observation": latest_observation(output_dir, clock=lambda: observed),
            "delivery_operations": delivery_snapshot(output_dir, clock=lambda: observed),
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


def _delivery_html(delivery):
    if not isinstance(delivery, dict):
        return '<p class="empty">尚无播报运行状态快照。</p>'
    observation = delivery.get("runtime_observation") or {}
    labels = {"not_observed": "尚未观察", "current": "已保存近期观察", "stale": "观察已过时",
              "future": "观察时间晚于报告时间", "unreadable": "记录不可读",
              "registered_enabled": "已注册并启用", "registered_disabled": "已注册但停用",
              "not_registered": "未注册", "configured_unverified": "已配置，账户未验证",
              "not_configured": "未配置", "ready": "持久环境与依赖已通过本地检查",
              "interpreter_missing": "缺少解释器", "missing_dependencies": "依赖未齐全",
              "not_persistent": "解释器不是持久环境", "probe_failed": "环境检查未完成",
              "submitted": "已提交服务商", "review_required": "需要检查，未自动重发",
              "provider_sent": "服务商报告已发送，尚无送达计数",
              "provider_confirmed": "服务商报告有送达记录（不等于收件箱）",
              "not_confirmed": "尚未获得送达证据", "not_recorded": "尚无回执",
              "creating": "正在创建提交记录", "submitting": "提交结果待确认",
              "in_progress": "提交过程已有记录", "attention_required": "需要检查",
              "before_delivery": "尚未到纽约 09:00", "delivery_due": "已到工作日交付时段",
              "weekend": "周末无需交付", "disabled": "配置停用", "enabled": "配置启用",
              "configuration_required": "配置需要完善", "numbers_only": "已保存数字版",
              "inputs_unavailable": "输入尚不可用", "verified": "保存的公网观察已核对",
              "text_verified": "文字已核对，音频尚不完整",
              "pending": "公网观察尚待完成", "not_generated": "尚无音频", "failed": "音频生成失败",
              "generating": "音频生成结果待确认", "integrity_failed": "音频记录校验失败",
              "missing": "尚无观察", "not_ready": "环境未就绪"}
    label = lambda value: labels.get(value, "未知状态") if isinstance(value, str) else "尚未观察"
    runtime = observation.get("runtime") or {}
    tasks = observation.get("tasks") or {}
    rows = [(name, label(tasks.get(name, {}).get("state")), tasks.get(name, {}).get("last_run_at"),
             tasks.get(name, {}).get("last_result"), tasks.get(name, {}).get("next_run_at")) for name in TASKS]
    html = (f'<p>任务与凭据：{_text(label(observation.get("state")))}；本地环境：{_text(label(runtime.get("state")))}。</p>'
            f'<p class="note">探测观察于 {_text(observation.get("observed_at"))}；距报告 {_text(observation.get("age_hours"))} 小时。'
            '页面和此报告仅读取保存的检查结果，不查询任务调度器或凭据存储。缺少新观察不能证明仍在自动运行。</p>')
    html += _table(("任务", "保存状态", "最近运行 · UTC", "退出结果", "下次运行 · UTC"), rows) if rows else ""
    credentials = observation.get("credentials") or {}
    html += _table(("凭据设置", "保存的配置状态"),
                   ((name, label(credentials.get(name, {}).get("state"))) for name in CREDENTIALS))
    tools = observation.get("tools") or {}
    html += _table(("本地工具", "保存的可用状态"),
                   ((name, "已发现工具" if tools.get(name, {}).get("available") is True else
                     "工具未找到" if tools.get(name, {}).get("available") is False else "尚未观察")
                    for name in ("git", "ffprobe")))
    email = delivery.get("email") or {}
    today = email.get("today") or {}
    credential = email.get("credential_configured")
    credential_label = "已配置，服务商账户未验证" if credential is True else "未配置" if credential is False else "尚未观察"
    html += (f'<p>邮件：{_text(label(email.get("configuration_state")))}；发送凭据：{_text(credential_label)}；'
             f'交付策略：{"允许文字邮件" if email.get("policy") == "allow_text" else "要求中英文音频"}。</p>'
             f'<p>纽约日期 {_text(today.get("date"))}：{_text(label(today.get("phase")))}，{_text(label(today.get("state")))}。</p>')
    today_languages = today.get("languages") or {}
    today_rows = [(lang, label(today_languages.get(lang, {}).get("state")),
                   label((today_languages.get(lang, {}).get("confirmation") or {}).get("state")),
                   today_languages.get(lang, {}).get("finished_at")) for lang in LANGUAGES]
    html += _table(("当天语言", "提交回执", "服务商送达观察", "回执时间 · UTC"), today_rows) if today_rows else ""
    counts = email.get("counts") or {}
    html += (f'<p class="note">近期保存回执：已提交 {_text(counts.get("submitted", 0))}，'
             f'待检查 {_text(counts.get("review_required", 0))}，服务商报告送达 {_text(counts.get("confirmed", 0))}。'
             '送达计数为已提交记录中的独立证据，不表示进入收件箱。过往回执不代替当天回执。</p>')
    history_rows = [(row.get("date"), lang, label(item.get("state")),
                     label((item.get("confirmation") or {}).get("state")), item.get("finished_at"))
                    for row in (email.get("history") or [])[:2] for lang in LANGUAGES
                    for item in [row.get("languages", {}).get(lang, {})]]
    html += _table(("最近历史日期", "语言", "提交状态", "服务商观察", "回执时间 · UTC"), history_rows) if history_rows else ""
    latest = (delivery.get("briefing") or {}).get("latest")
    if latest:
        html += (f'<p>最新保存稿：{_text(latest.get("date"))}，{_text(label(latest.get("state")))}；归因截至 '
                 f'{_text(latest.get("as_of"))}；生成于 {_text(latest.get("generated_at"))}；{_text(label(latest.get("freshness")))}。</p>')
        public = latest.get("public") or {}
        html += (f'<p class="note">公网：{_text(label(public.get("state")))}，观察于 {_text(public.get("observed_at"))}，'
                 f'{_text(label(public.get("freshness")))}。这项保存观察不能证明当前公网仍可访问。</p>')
        html += _table(("音频语言", "文件状态", "观察新鲜度", "生成时间 · UTC"),
                       ((lang, label(item.get("state")), label(item.get("freshness")), item.get("generated_at"))
                        for lang in LANGUAGES for item in [(latest.get("audio") or {}).get(lang, {})]))
    else:
        html += '<p class="empty">尚无保存稿件，未从历史页面推断今天已出刊。</p>'
    return html


def render_report(report):
    acceptance, archive = report["acceptance"], report["archive"]
    records, comparison = archive["verified_records"], archive["comparison"]
    engine_count = sum(r["kind"] == "engine_inputs" for r in records)
    baseline_count = sum(r["kind"] == "cache_baseline" for r in records)
    due_days = acceptance["days"]
    passed_days = sum(r["passed"] for r in due_days)
    labels = {"delivered": "最近使用日已有交付", "no_activity": "尚无实际使用记录",
              "waiting": "等待可用输入", "attention": "实际运行需要检查",
              "completion_unconfirmed": "运行完成情况待确认", "observed_no_delivery": "已观察到调用，尚无交付"}
    status = labels[acceptance["state"]]
    tone = "good" if acceptance["state"] == "delivered" else "pending"
    if archive["issues"] or comparison["state"] == "comparison_failed":
        status, tone = "留档需要检查", "pending"
    day_html = '<p class="empty">尚无实际使用记录。没有调用记录的日期不计为故障，也无法据此判断电脑是否开机。</p>'
    if due_days:
        parts = []
        for row in reversed(due_days[-20:]):
            checks = "".join(f'<li class="{"ok" if value else "fail"}">{"通过" if value else "未通过"} · '
                             f'{_text(CHECK_LABELS.get(key, key))}</li>' for key, value in row["checks"].items())
            problems = _table(("文件", "记录问题"), ((r["file"], r["reason"]) for r in row["record_issues"])) if row["record_issues"] else ""
            context = row["context"]
            context_text = f'通过规则检查的解读 {_text(context["verified_notes"])} 条'
            if context["generation_failed"]:
                context_text += '，存在生成失败，覆盖不完整'
            elif context.get("rejected_notes"):
                context_text += f'，{context["rejected_notes"]} 条草稿未通过检查'
            if context.get("draft_evidence") == "unreadable_or_mismatched":
                context_text += '，草稿证据无法核对'
            automatic = '原始自动生成与交付链已核对' if row["automation"] == "confirmed" else '原始自动生成来源证据不足；后来的定时检查不补作证明'
            problems += f'<p class="note">已记录执行问题：{_text(", ".join(row["failures"]))}</p>' if row["failures"] else ''
            parts.append(f'<details class="day"><summary><span class="mono">{_text(row["date"])}</span>'
                         f'<span>{"交付证据已核对" if row["passed"] else _text(labels[row["state"]])} · {context_text}</span></summary>'
                         f'<p class="note">归因截至 {_text(row["attribution_as_of"])}；生成于 {_text(row["generated_at"])}；发布于 {_text(row["published_at"])}</p>'
                         f'<p class="note">{automatic}</p>'
                         f'<p class="note">公网核验：{_text(row.get("public_pages_delivery", "not_checked"))}；观察于 {_text(row.get("public_pages_observed_at"))}。这项记录不追溯证明原始发布时间的可访问性。</p>'
                         + (f'<ul class="checks">{checks}</ul>' if row["edition_state"] != "missing" or row["state"] == "attention" else
                            f'<p class="note">当前观察：{_text(row["waiting_reason"] or row["state"])}。尚未交付不等于生成失败。</p>')
                         + f'{problems}<p class="muted">未结束调用：{_text(row["unfinished_invocations"])}</p></details>')
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
        meaning = ("启动时已超过可选晨间窗口。这个时钟观察不计为故障，实际交付以上方使用记录为准。"
                   if gate["state"] == "missed_window" else
                   "周末无需出刊。" if gate["phase"] == "weekend" else
                   "尚未进入晨间窗口。" if gate["phase"] == "before_window" else
                   "窗口已结束，已保存晨报与匹配的推送回执。")
        clock_html = (f'<p class="empty">{meaning}</p><p class="mono small">观察时间：{_text(gate["observed_at"])}</p>'
                      '<p class="note">窗口外只记录状态，不补抓新闻、不调用模型、不补造晨报、不发布。'
                      '独立补发任务会在可用时运行。推送回执不能证明公网部署完成。</p>')
    scheduled = report.get("scheduled_acceptance", {})
    legacy_html = (f'<p class="note">旧准时口径：连续 {_text(scheduled.get("consecutive_passes", 0))} / '
                   f'{_text(scheduled.get("required_consecutive_weekdays", 5))} 个工作日。'
                   '这里只保留可选能力诊断，不作为当前项目验收门槛。旧报告与稿件未改写。</p>')
    fonts, licenses = _font_styles()
    values = {"FONTS": fonts, "LICENSES": licenses, "STATUS": _text(status), "TONE": tone,
              "OBSERVED": _text(report["observed_at"]),
              "LOCAL_TIME": _text(observed.astimezone(ZoneInfo(M.ZONE)).strftime("%Y-%m-%d %H:%M:%S %Z")),
              "ENGINE": str(engine_count), "BASELINE": str(baseline_count),
              "START": _text(acceptance["start_date"]), "DUE": str(len(due_days)), "PASSED": str(passed_days),
              "AUTOMATED": str(acceptance["automated_days"]), "GENERATION_ISSUES": str(acceptance["generation_issue_days"]),
              "LEGACY": legacy_html,
              "CONTEXT": str(acceptance["event_context_days"]), "DAYS": day_html, "CLOCK": clock_html, "INVENTORY": inventory,
              "DELIVERY": _delivery_html(report.get("delivery_operations")),
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
