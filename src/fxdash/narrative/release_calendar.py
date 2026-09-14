"""Observed official US release schedules. No forecasts, actuals or full-market claim."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from . import morning as M

SOURCES = {
    'BLS':'https://www.bls.gov/schedule/news_release/bls.ics',
    'BEA':'https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics',
}
REVISION = 'official-us-calendar-1'


def fetch(url):
    import requests
    if url not in SOURCES.values():
        raise ValueError('unsupported_calendar_source')
    with requests.get(url, timeout=(5, 12), allow_redirects=False, stream=True) as response:
        if response.status_code != 200:
            raise ValueError('calendar_source_unavailable')
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 1_000_000:
                raise ValueError('calendar_too_large')
            chunks.append(chunk)
    return b''.join(chunks).decode('utf-8-sig')


def parse_ics(text, source):
    """Accept explicit instants only; unsupported recurrence/floating dates fail closed."""
    if source not in SOURCES or len(text) > 1_000_000:
        raise ValueError('invalid_calendar')
    lines = []
    for line in text.splitlines():
        if not line:
            continue  # BEA serves CR CR LF, yielding empty lines after splitting.
        if line[0] in ' \t' and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    if not lines or lines[0] != 'BEGIN:VCALENDAR' or lines[-1] != 'END:VCALENDAR':
        raise ValueError('invalid_calendar_envelope')
    entries, current, skipped, seen = {}, None, 0, 0
    for line in lines:
        if line == 'BEGIN:VEVENT':
            if current is not None:
                raise ValueError('nested_calendar_event')
            current = {}
        elif line == 'END:VEVENT':
            if current is None:
                raise ValueError('invalid_calendar_event')
            seen += 1
            try:
                if any(k in current for k in ('RRULE','RDATE','RECURRENCE-ID')):
                    raise ValueError('unsupported_recurrence')
                uid = current['UID'][1]
                if not uid or len(uid) > 500:
                    raise ValueError('invalid_uid')
                sequence = int(current.get('SEQUENCE', ('','0'))[1])
                if current.get('STATUS', ('',''))[1] == 'CANCELLED':
                    value = None
                else:
                    params, stamp = current['DTSTART']
                    if stamp.endswith('Z'):
                        instant = datetime.strptime(stamp, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
                    elif params in {'TZID=America/New_York','TZID=US/Eastern'}:
                        instant = datetime.strptime(stamp, '%Y%m%dT%H%M%S').replace(tzinfo=ZoneInfo(M.ZONE))
                    else:
                        raise ValueError('calendar_time_not_explicit')
                    title = re.sub(r'\\([nN,;\\])', lambda m:' ' if m[1].lower()=='n' else m[1], current['SUMMARY'][1])
                    title = ' '.join(title.split())
                    if not title or len(title) > 350:
                        raise ValueError('invalid_calendar_title')
                    value = {'source':source, 'source_url':SOURCES[source], 'title':title,
                             'scheduled_at':instant.astimezone(timezone.utc).isoformat(),
                             'timezone':M.ZONE, 'id':hashlib.sha256((source+uid).encode()).hexdigest()[:24]}
                if uid not in entries or sequence > entries[uid][0]:
                    entries[uid] = (sequence, value)
                elif sequence == entries[uid][0] and value != entries[uid][1]:
                    entries[uid] = (sequence, None)
                    raise ValueError('ambiguous_event_revision')
            except (KeyError, ValueError):
                skipped += 1
            current = None
        elif current is not None and ':' in line:
            key, value = line.split(':', 1)
            name, _, params = key.partition(';')
            if name in current:
                raise ValueError('duplicate_calendar_property')
            current[name] = (params, value)
    if current is not None or not seen:
        raise ValueError('incomplete_calendar')
    return [v for _,v in entries.values() if v is not None], skipped


def capture(output_dir, *, clock=M.now_utc, fetcher=None):
    moment = clock()
    day = M.local_time(moment).date()
    root = Path(output_dir)/'calendar'/day.isoformat()
    with M.DayLock(root/'capture.lock'):
        old = M.read_json(root/'latest.json')
        try:
            age = (moment-datetime.fromisoformat(old['observed_at'])).total_seconds()
            if 0 <= age < 3600 and old.get('revision') == REVISION:
                return old
        except (KeyError, ValueError, TypeError):
            pass
        events, sources = [], []
        for source, url in SOURCES.items():
            row = {'source':source, 'url':url, 'state':'unavailable'}
            try:
                text = (fetcher or fetch)(url)
                parsed, skipped = parse_ics(text, source)
                digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
                source_file = root/'sources'/(source+'-'+digest+'.ics')
                source_file.parent.mkdir(parents=True,exist_ok=True)
                if not source_file.exists():
                    with source_file.open('xb') as stream:
                        stream.write(text.encode('utf-8'))
                row.update(state='partial' if skipped else 'available', skipped_events=skipped,
                           content_sha256=digest, input_file=str(source_file.relative_to(Path(output_dir))).replace('\\','/'))
                row['last_scheduled_date'] = max((M.local_time(datetime.fromisoformat(e['scheduled_at'])).date().isoformat()
                                                   for e in parsed), default=None)
                if not row['last_scheduled_date'] or row['last_scheduled_date'] < day.isoformat():
                    row['state'] = 'coverage_expired'
                for event in parsed:
                    date = M.local_time(datetime.fromisoformat(event['scheduled_at'])).date()
                    if day <= date <= day+timedelta(days=7):
                        events.append(event)
            except Exception as exc:
                row['error_type'] = type(exc).__name__
            row['observed_at'] = clock().isoformat(timespec='seconds')
            sources.append(row)
        result = {'revision':REVISION, 'date':day.isoformat(), 'timezone':M.ZONE,
                  'observed_at':clock().isoformat(timespec='seconds'), 'coverage':'US BLS and BEA only',
                  'state':'available' if all(s['state']=='available' for s in sources) else
                          'partial' if any(s['state'] in {'available','partial'} for s in sources) else 'unavailable',
                  'sources':sources, 'events':sorted(events, key=lambda e:(e['scheduled_at'], e['id']))[:20]}
        M.atomic_json(root/(moment.strftime('%H%M%S%f')+'.json'), result)
        M.atomic_json(root/'latest.json', result)
        return result


def attach(packet, output_dir, *, clock=M.now_utc):
    """Only new packets receive context; existing saved packets remain unchanged."""
    try:
        packet['calendar'] = capture(output_dir, clock=clock)
    except Exception:
        packet['calendar'] = {'revision':REVISION, 'state':'unavailable', 'events':[],
                              'date':M.local_time(clock()).date().isoformat(), 'observed_at':clock().isoformat(timespec='seconds'),
                              'coverage':'US BLS and BEA only', 'timezone':M.ZONE, 'sources':[]}
    packet['fetched_at'] = clock().isoformat(timespec='seconds')


def public_context(packet, generated_at):
    """Project only allowlisted, correctly timed source records from the frozen input."""
    context = packet.get('calendar')
    if not isinstance(context, dict):
        return None
    try:
        observed = datetime.fromisoformat(context['observed_at'])
        generated = datetime.fromisoformat(generated_at)
        if (not observed.tzinfo or not generated.tzinfo or observed > generated
                or context.get('revision') != REVISION or context.get('timezone') != M.ZONE
                or context.get('state') not in {'available','partial','unavailable'}
                or M.local_time(observed).date().isoformat() != context['date']
                or M.local_time(generated).date().isoformat() != context['date']):
            raise ValueError('invalid_calendar_observation')
        result = {k:context[k] for k in ('revision','state','date','timezone','observed_at','coverage')}
        result['sources'] = [{k:s[k] for k in ('source','url','state','observed_at')} for s in context['sources']
                             if s.get('url') == SOURCES.get(s.get('source'))]
        result['events'] = [{k:e[k] for k in ('id','title','source','source_url','scheduled_at','timezone')}
                            for e in context['events'] if e.get('source_url') == SOURCES.get(e.get('source'))]
        return result
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


def read_latest(output_dir):
    root = Path(output_dir)/'calendar'
    if not root.exists():
        return None
    paths = sorted((p for p in root.glob('????-??-??/latest.json') if not p.is_symlink()),reverse=True)
    for path in paths[:1]:
        value = M.read_json(path)
        return public_context({'calendar':value}, value.get('observed_at'))
    return None


def spoken_watch(packet, generated_at, lang):
    """One short code-owned schedule item, never an invented actual or forecast."""
    context = public_context(packet,generated_at)
    if not context:
        return None
    generated = datetime.fromisoformat(generated_at)
    upcoming = [e for e in context['events'] if datetime.fromisoformat(e['scheduled_at'])>generated and len(e['title'])<=160]
    if not upcoming:
        return ('保存的美国日历中暂无后续可用日程，覆盖仅限劳工统计局和经济分析局。' if lang=='zh' else
                'The saved US calendar has no usable upcoming item. Coverage is limited to BLS and BEA.')
    event = upcoming[0]
    stamp = M.local_time(datetime.fromisoformat(event['scheduled_at']))
    if lang=='zh':
        return f"日历关注：{event['source']}预定在纽约时间{stamp.month}月{stamp.day}日{stamp.hour}点{stamp.minute:02d}分发布{event['title']}。时间可能调整，请核对官方来源。"
    return (f"On the saved US calendar, {event['source']} schedules {event['title']} for "
            f"{stamp:%B} {stamp.day} at {stamp:%H:%M} New York time. Check the official source for changes.")
