"""Narrow, auditable exclusions for display/news-context retrieval.

The residual narrative's recall and frozen archives remain unchanged. Rules reject
explicit page types or institutional name collisions, not competing explanations.
"""
import re

QUOTE = re.compile(r"streaming chart|live chart|\bquote\s*$", re.I)
FUND = re.compile(r"investment management|sovereign (?:wealth )?fund|shopping cent(?:er|re)|"
                  r"stake in|shareholding|holdings? disclosure|form 8\.3|buys? .{0,20}stake|"
                  r"acquires? .{0,30}(?:portfolio|property|properties)", re.I)
MACRO = re.compile(r"interest rates?|policy rate|rate (?:cut|hike|decision)|inflation|"
                   r"monetary policy|\bkrone\b|\bNOK\b|currency|foreign exchange|"
                   r"treasur\w*|government (?:bonds?|debt)|bond (?:investments?|yields?)|hedging", re.I)


def exclusion_reason(title: str, pair: str | None = None) -> str | None:
    if QUOTE.search(title):
        return "quote_or_chart_page"
    if (pair == "USDNOK" or "norges bank" in title.lower()) and FUND.search(title) and not MACRO.search(title):
        return "sovereign_fund_investment_without_fx_or_policy_context"
    return None
