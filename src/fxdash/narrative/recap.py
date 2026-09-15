"""Short recap from validated frozen facts, without new model or news calls.

At most two percentage moves, one model observation and one checked news item.
Detailed figures and data-status flags remain in the original edition.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import math
import re

from . import audio_script as S, morning as M

VERSION = 'market-recap-v1'
QUIET_PERCENT = 0.05


def price_change(row):
    """Simple percentage change in USD/XXX, not the reciprocal currency quote."""
    try:
        return S.number(math.expm1(S.number(row['y'])) * 100)
    except OverflowError:
        raise ValueError('recap_return_out_of_bounds') from None


def percentage(value, lang):
    value = f'{abs(value):.1f}'.removesuffix('.0')
    return f'{value}%' if lang == 'zh' else f'{value} percent'


def direction(rows):
    up = sum(price_change(r) >= QUIET_PERCENT for r in rows)
    down = sum(price_change(r) <= -QUIET_PERCENT for r in rows)
    if up >= 4 and down <= 1:
        return 'up'
    if down >= 4 and up <= 1:
        return 'down'
    if up and down:
        return 'mixed'
    return 'some_up' if up else 'some_down' if down else 'quiet'


def market(rows, lang):
    zh = lang == 'zh'
    tone = direction(rows)
    opening = {
        'up': ('The dollar was broadly stronger across the currencies we track.', '我们跟踪的这几种货币中，美元整体偏强。'),
        'down': ('The dollar was broadly weaker across the currencies we track.', '我们跟踪的这几种货币中，美元整体偏弱。'),
        'mixed': ('It was a mixed session for the dollar, with currencies moving in different directions.', '美元这一天的表现有涨有跌，各货币之间出现了分化。'),
        'some_up': ('The dollar gained against some currencies, while the rest were little changed.', '美元对部分货币走高，其余变化不大。'),
        'some_down': ('The dollar slipped against some currencies, while the rest were little changed.', '美元对部分货币走低，其余变化不大。'),
        'quiet': ('It was a quiet session across the six currency pairs we track, with little change against the dollar.', '我们跟踪的六个货币对这一天都比较平静，兑美元的变化不大。'),
    }[tone][int(zh)]
    active = sorted((r for r in rows if abs(price_change(r)) >= QUIET_PERCENT),
                    key=lambda r: (-abs(price_change(r)), r['pair']))[:2]
    phrases = [opening]
    if len(active) == 2 and (price_change(active[0]) > 0) == (price_change(active[1]) > 0):
        names = [S.PAIRS[r['pair']][int(zh)] for r in active]
        amounts = [percentage(price_change(r), lang) for r in active]
        rising = price_change(active[0]) > 0
        if zh:
            phrases.append(f"美元兑{names[0]}和{names[1]}的{'涨幅' if rising else '跌幅'}最明显，分别约为{amounts[0]}和{amounts[1]}。")
        else:
            phrases.append(f"Its biggest {'gains' if rising else 'losses'} were against {names[0]} and {names[1]}, at about {amounts[0]} and {amounts[1]} respectively.")
    else:
        for r in active:
            name, change = S.PAIRS[r['pair']][int(zh)], price_change(r)
            phrases.append(f"美元兑{name}{'上涨' if change > 0 else '下跌'}约{percentage(change, lang)}。" if zh else
                           f"Against {name}, it {'rose' if change > 0 else 'fell'} about {percentage(change, lang)}.")
    quiet = [r for r in rows if abs(price_change(r)) < QUIET_PERCENT]
    if len(quiet) == 1:
        name = S.PAIRS[quiet[0]['pair']][int(zh)]
        phrases.append(f'兑{name}则基本持平。' if zh else f'Against {name}, it was little changed.')
    return ('' if zh else ' ').join(phrases), tone


def model_observation(rows, lang):
    """Only a substantial mismatch is worth interrupting the market recap for."""
    notable = [r for r in rows if abs(price_change(r)) >= 0.1
               and abs(r['residual']) >= max(0.003, abs(r['y']) * 0.5)]
    if not notable:
        return None
    row = max(notable, key=lambda r: (abs(r['residual']), r['pair']))
    name = S.PAIRS[row['pair']][int(lang == 'zh')]
    return (f'{name}这一边还值得多看一眼，现有因子没能很好地解释这次波动，具体原因还需要进一步核对。'
            if lang == 'zh' else f'The move against {name} is harder to explain with our usual factors. '
            'It deserves a closer look before attributing it to any one event.')


def news(edition, packet, lang):
    """Use a complete short checked event; never truncate it or invent a cause."""
    from .driver_notes import validate, source_set
    rows = {r['pair']: r for r in packet['pairs']}
    for item in edition['notes']:
        row, note = rows.get(item.get('pair')), item.get('note')
        if not row or not note or note.get('assessment') == 'insufficient_evidence':
            continue
        if validate(note, row, source_set(packet, row), packet['fetched_at']):
            continue
        event = note[lang]['event'].strip()
        if len(event) > (110 if lang == 'zh' else 260):
            continue
        if re.search(r'\bbp\b|basis points|residual|regression|[A-Z]+_LOO|残差|回归|基点|贡献', event, re.I):
            continue
        return ('消息面上，' if lang == 'zh' else 'For context, ') + event
    return None


def calendar(edition, packet, lang):
    from .release_calendar import public_context
    context = public_context(packet, edition['generated_at'])
    if not context:
        return None
    generated = datetime.fromisoformat(edition['generated_at'])
    upcoming = []
    for event in context['events']:
        try:
            stamp = datetime.fromisoformat(event['scheduled_at'])
            if stamp.tzinfo and generated < stamp <= generated + timedelta(days=7) and len(event['title']) <= 110:
                upcoming.append((stamp, event))
        except (KeyError, ValueError, TypeError):
            continue
    if not upcoming:
        return None
    stamp, event = min(upcoming, key=lambda item: (item[0], item[1]['id']))
    stamp = M.local_time(stamp)
    if lang == 'zh':
        return f"日程上，{event['title']}预定在纽约时间{stamp.month}月{stamp.day}日{stamp.hour}点{stamp.minute:02d}分公布。"
    return (f"On the US calendar, {event['title']} is scheduled for {stamp:%B} {stamp.day} "
            f"at {stamp:%H:%M} New York time.")


def compose(edition, packet, lang):
    """Called after audio_script's date, six-pair and evidence-hash checks."""
    zh = lang == 'zh'
    opening = (f"先回顾一下{S.spoken_date(packet['as_of'], lang)}的汇市。" if zh else
               f"Let's look back at {S.spoken_date(packet['as_of'], lang)}.")
    overview, tone = market(packet['pairs'], lang)
    lines = [opening, overview]
    for extra in (model_observation(packet['pairs'], lang), news(edition, packet, lang)):
        if extra:
            lines.append(extra)
    watch = calendar(edition, packet, lang)
    if not watch:
        watch = {
            'up': ('Next, watch whether the dollar can hold on to those gains.', '接下来可以留意，美元这波走强能不能延续。'),
            'down': ('Next, watch whether the dollar can recover some ground.', '接下来可以留意，美元能否收复一些跌幅。'),
            'mixed': ('Next, watch whether those differences between currencies persist.', '接下来可以留意，各货币之间的强弱分化是否延续。'),
        }.get(tone, ('Next, watch for a clearer direction as new information comes in.', '接下来可以结合新的消息，观察汇率方向是否更加清晰。'))[int(zh)]
    lines.append(watch)
    lines.append('详细图表、归因拆解和新闻来源都在网站上。' if zh else
                 'The charts, full breakdown and news sources are on the dashboard.')
    return '\n\n'.join(lines)
