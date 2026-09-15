"""Opt-in email campaigns with provider-hosted signup and no local address list.

Disabled until operator setup. No API calls on page reads or static builds.
An uncertain submission is never repeated automatically.
"""
from __future__ import annotations

from datetime import datetime, time
from html import escape
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from . import morning as M, public_delivery as P, audio_briefing as B


def form_url(value):
    if not isinstance(value,str) or len(value)>2048 or any(ord(c)<33 for c in value):
        return None
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None,443) or parsed.query or parsed.fragment
            or not re.fullmatch(r'[a-z0-9-]+\.sibforms\.com', parsed.hostname)
            or not re.fullmatch(r'/serve/[A-Za-z0-9_-]+={0,2}', parsed.path)):
        return None
    return value


def config(output_dir):
    value = M.read_json(Path(output_dir)/'subscriptions'/'config.json')
    return validate_settings(value)


def validate_settings(value):
    if value.get('enabled') is not True:
        return None
    if (value.get('double_opt_in_confirmed') is not True or value.get('quota_approved') is not True
            or type(value.get('sender_id')) is not int or value['sender_id']<=0
            or not isinstance(value.get('sender_footer'),str) or not 10<=len(value['sender_footer'])<=500):
        return None
    forms, lists = value.get('forms') or {}, value.get('lists') or {}
    if (set(forms) != {'en','zh'} or set(lists) != {'en','zh'}
            or not all(form_url(forms[l]) and type(lists[l]) is int and lists[l]>0 for l in ('en','zh'))
            or lists['en']==lists['zh']):
        return None
    return value


def public_config(output_dir):
    try:
        settings = config(output_dir)
    except (ValueError,TypeError,AttributeError):
        settings = None
    if not settings:
        return {'enabled':False}
    return {'enabled':True,'provider':'Brevo','forms':settings['forms'],
            'timezone':M.ZONE,'target_time':'09:00','weekdays_only':True,'late_delivery_possible':True}


def api_key():
    # The scheduler may have an older inherited environment. Read just this user's
    # literal key when present, without logging it or importing other settings.
    value = os.environ.get('BREVO_API_KEY','')
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment',0,winreg.KEY_READ) as handle:
                saved, kind = winreg.QueryValueEx(handle,'BREVO_API_KEY')
            value = saved if kind == winreg.REG_SZ else ''
        except FileNotFoundError:
            pass
        except OSError:
            value = ''
    if (not isinstance(value,str) or not 10<=len(value)<=512 or not value.strip('*')
            or any(not 33<=ord(c)<=126 for c in value)):
        raise ValueError('email_key_not_configured')
    return value


class Provider:
    def __init__(self):
        self._key = api_key()

    def request(self, method, path, payload=None):
        import requests
        if not re.fullmatch(r'/emailCampaigns(?:/\d+(?:/sendNow)?)?',path):
            raise ValueError('unsupported_email_operation')
        response = requests.request(method,'https://api.brevo.com/v3'+path,
                                    headers={'api-key':self._key}, json=payload,
                                    timeout=(5,20), allow_redirects=False)
        if response.status_code not in {200,201,202,204} or len(response.content)>2_000_000:
            raise ValueError('email_provider_rejected')
        return response.json() if response.content else {}

    def create(self,payload):
        value = self.request('POST','/emailCampaigns',payload)
        if type(value.get('id')) is not int or value['id']<=0:
            raise ValueError('email_campaign_id_missing')
        return value['id']

    def send(self,identity):
        self.request('POST',f'/emailCampaigns/{identity}/sendNow')


def email_text(brief, lang):
    """Omit per-pair status labels in email, retaining the frozen source text.

    Match only the numeric-summary annotation, not that word in news reporting.
    Data status stays available on the linked dashboard and in archived evidence.
    """
    recap = brief.get('recap') or {}
    if recap.get('version') == 'market-recap-v1' and recap.get('text', {}).get(lang):
        return recap['text'][lang]
    annotation = r' \(provisional\)(?=; residual )' if lang == 'en' else r'（待确认）(?=，残差 )'
    return re.sub(r'(\bUSD/[A-Z]{3} [+-]\d+\.\d+ bp)' + annotation,
                  r'\1', brief['text'][lang])


def payload(settings, brief, audio, lang):
    zh = lang == 'zh'
    title = ('外汇简报 ' if zh else 'FX briefing ')+brief['date']
    body = '</p><p>'.join(escape(p) for p in email_text(brief, lang).split('\n\n') if p.strip())
    intro = ('本期为开机后补发。' if zh else 'This edition was prepared after the host became available.') if brief['mode']=='catchup' else ''
    text = f'<h1>{escape(title)}</h1><p>{intro}</p><p>{body}</p>'
    calendar = brief.get('calendar') or {}
    if calendar.get('events'):
        from zoneinfo import ZoneInfo
        text += '<h2>'+('发布日程（纽约时间）' if zh else 'Release schedule (New York time)')+'</h2><ul>'
        for event in calendar['events'][:3]:
            stamp = datetime.fromisoformat(event['scheduled_at']).astimezone(ZoneInfo(M.ZONE))
            text += f'<li>{stamp:%Y-%m-%d %H:%M} {escape(event["title"])} ({escape(event["source"])})</li>'
        text += '</ul><p>'+('仅覆盖 BLS 与 BEA 预定发布，不含实际值和市场预期。' if zh else 'BLS and BEA scheduled releases only; actual values and consensus are not included.')+'</p>'
    text += f'<p><a href="{P.SITE+audio["url"]}">'+('收听本期语音' if zh else 'Listen to this edition')+'</a></p>'
    text += f'<p><a href="{P.SITE}#/news">'+('查看网页与来源' if zh else 'Read the dashboard and sources')+'</a></p>'
    text += '<p>'+('您已订阅 FX Dashboard 的工作日简报。语音为合成语音。' if zh else 'You subscribed to FX Dashboard weekday briefings. Audio uses a synthetic voice.')+'</p>'
    text += f'<p>{escape(settings["sender_footer"])}</p>'
    text += '<p><a href="{{ unsubscribe }}">'+('退订' if zh else 'Unsubscribe')+'</a></p>'
    return {'name':f'fx-{brief["date"]}-{lang}-{brief["edition_hash"][:12]}',
            'sender':{'id':settings['sender_id']}, 'subject':title,'htmlContent':text,
            'recipients':{'listIds':[settings['lists'][lang]]}, 'mirrorActive':False}


def deliver(output_dir, brief, verified, *, clock=M.now_utc, provider_factory=None):
    settings = config(output_dir)
    if not settings:
        return {'state':'disabled'}
    moment = clock()
    local = M.local_time(moment)
    if local.weekday()>=5 or local.time()<time(9) or brief.get('date')!=local.date().isoformat():
        return {'state':'outside_delivery_day'}
    expected = P.expectation(output_dir,brief)
    try:
        age = (moment-datetime.fromisoformat(verified['observed_at'])).total_seconds()
        if (verified['state']!='verified' or verified['expectation_hash']!=M.digest(expected)
                or verified['edition_hash']!=brief['edition_hash'] or not 0<=age<=900
                or set(verified['audio_verified'])!={'en','zh'}):
            return {'state':'public_delivery_unconfirmed'}
    except (KeyError,TypeError,ValueError):
        return {'state':'public_delivery_unconfirmed'}
    try:
        provider = (provider_factory or Provider)()
    except Exception:
        return {'state':'configuration_required'}
    states = {}
    # One recipient-language campaign per NY date, even if the selected edition
    # or config changes later. Never send an old backlog after a long shutdown.
    root = Path(output_dir)/'subscriptions'/'deliveries'/brief['date']
    for lang in ('en','zh'):
        path = root/(lang+'.json')
        try:
            with M.DayLock(root/(lang+'.lock')):
                previous = M.read_json(path)
                if path.exists():
                    states[lang] = previous.get('state','review_required')
                    continue
                message = payload(settings,expected,expected['media'][lang],lang)
                row = {'date':brief['date'],'language':lang,'edition_hash':brief['edition_hash'],
                       'payload_hash':M.digest(message),'state':'creating', 'started_at':moment.isoformat()}
                M.atomic_json(path,row)
                try:
                    identity = provider.create(message)
                    row.update(campaign_id=identity,state='submitting')
                    M.atomic_json(path,row)
                    provider.send(identity)
                    row.update(state='submitted',finished_at=clock().isoformat())
                except Exception as exc:
                    row.update(state='review_required',error_type=type(exc).__name__,finished_at=clock().isoformat())
                M.atomic_json(path,row)
                states[lang] = row['state']
        except M.Busy:
            states[lang] = 'busy'
    return {'state':'submitted' if set(states.values())=={'submitted'} else 'attention_required',
            'languages':states,'scope':'provider_submission_not_inbox_receipt'}


def main(argv=None):
    import argparse
    import sys
    from ..config import OUTPUT_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check',action='store_true')
    group.add_argument('--configure',action='store_true',help='Read operator settings, without keys, from stdin')
    group.add_argument('--disable',action='store_true')
    args = parser.parse_args(argv)
    if args.disable:
        value = M.read_json(OUTPUT_DIR/'subscriptions/config.json')
        value['enabled'] = False
        M.atomic_json(OUTPUT_DIR/'subscriptions/config.json',value)
    if args.configure:
        value = json.loads(sys.stdin.read(12000))
        allowed = {'enabled','double_opt_in_confirmed','quota_approved','sender_id','sender_footer','forms','lists'}
        if set(value)-allowed or validate_settings(value) is None:
            raise ValueError('invalid_email_settings')
        M.atomic_json(OUTPUT_DIR/'subscriptions/config.json',value)
    key_ok = False
    if config(OUTPUT_DIR):
        try:
            api_key()
            key_ok = True
        except ValueError:
            pass
    print(json.dumps({'enabled':bool(config(OUTPUT_DIR)),'key_format_valid':key_ok,'network_called':False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
