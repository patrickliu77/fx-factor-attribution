"""Opt-in email campaigns with provider-hosted signup and no local address list.

Disabled until operator setup. No API calls on page reads or static builds.
An uncertain submission is never repeated automatically.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from html import escape
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from . import morning as M, public_delivery as P, audio_briefing as B


POLICIES = {'require_audio', 'allow_text'}
PROVIDER_CODES = frozenset({'invalid_parameter', 'missing_parameter', 'out_of_range',
    'unauthorized', 'document_not_found', 'method_not_allowed', 'not_enough_credits',
    'duplicate_parameter', 'duplicate_request', 'account_under_validation', 'permission_denied'})
PROVIDER_STATUSES = frozenset({'draft', 'sent', 'archive', 'queued', 'suspended',
    'inProcess', 'inReview', 'queuedForSmtp', 'queuedForTrigger'})
PROVIDER_COUNTS = frozenset({'sent', 'delivered', 'softBounces', 'hardBounces',
    'complaints', 'unsubscriptions', 'uniqueViews', 'viewed', 'uniqueClicks', 'clickers',
    'deferred', 'remaining'})
PROVIDER_TIMES = frozenset({'createdAt', 'modifiedAt', 'scheduledAt', 'sentDate'})


class EmailProviderError(RuntimeError):
    """A safe diagnostic; neither provider bodies nor transport messages survive."""
    def __init__(self, stage, http_status=None, provider_code=None, uncertain_outcome=False):
        self.stage = stage if stage in {'create', 'send', 'reconcile'} else 'reconcile'
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.provider_code = provider_code if isinstance(provider_code, str) and provider_code in PROVIDER_CODES else None
        self.uncertain_outcome = uncertain_outcome is True
        super().__init__('email_provider_error:' + json.dumps(self.diagnostic(), sort_keys=True))

    def diagnostic(self):
        return {'stage': self.stage, 'http_status': self.http_status,
                'provider_code': self.provider_code, 'uncertain_outcome': self.uncertain_outcome}


def delivery_policy(settings):
    return settings.get('delivery_policy', 'require_audio')


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
    if (not isinstance(delivery_policy(value), str) or delivery_policy(value) not in POLICIES
            or value.get('double_opt_in_confirmed') is not True or value.get('quota_approved') is not True
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
        if method == 'POST' and path == '/emailCampaigns':
            stage = 'create'
        elif method == 'POST' and re.fullmatch(r'/emailCampaigns/[1-9][0-9]*/sendNow', path):
            stage = 'send'
        elif method == 'GET' and re.fullmatch(r'/emailCampaigns/[1-9][0-9]*', path) and payload is None:
            stage = 'reconcile'
        else:
            raise ValueError('unsupported_email_operation')
        options = {'headers': {'api-key': self._key}, 'timeout': (5, 20), 'allow_redirects': False}
        if stage == 'reconcile':
            options['params'] = {'statistics': 'globalStats', 'excludeHtmlContent': 'true'}
        else:
            options['json'] = payload
        try:
            response = requests.request(method, 'https://api.brevo.com/v3' + path, **options)
        except Exception:
            raise EmailProviderError(stage, uncertain_outcome=stage != 'reconcile') from None
        status = response.status_code
        if type(status) is not int or status not in {200, 201, 202, 204}:
            code = None
            try:
                if len(response.content) <= 2_000_000:
                    body = response.json()
                    code = body.get('code') if isinstance(body, dict) else None
            except Exception:
                pass
            raise EmailProviderError(stage, status, code,
                uncertain_outcome=stage != 'reconcile' and (type(status) is not int or status >= 500 or status == 408)) from None
        try:
            if len(response.content) > 2_000_000:
                raise ValueError()
            value = response.json() if response.content else {}
            if not isinstance(value, dict):
                raise ValueError()
            if stage in {'create', 'reconcile'} and (type(value.get('id')) is not int or value['id'] <= 0):
                raise ValueError()
        except Exception:
            raise EmailProviderError(stage, status, uncertain_outcome=stage != 'reconcile') from None
        return value

    def create(self,payload):
        value = self.request('POST','/emailCampaigns',payload)
        if type(value.get('id')) is not int or value['id']<=0:
            raise EmailProviderError('create', uncertain_outcome=True)
        return value['id']

    def send(self,identity):
        if type(identity) is not int or identity <= 0:
            raise ValueError('invalid_email_campaign_id')
        self.request('POST',f'/emailCampaigns/{identity}/sendNow')

    def report(self, identity):
        if type(identity) is not int or identity <= 0:
            raise ValueError('invalid_email_campaign_id')
        value = self.request('GET', f'/emailCampaigns/{identity}')
        if type(value.get('id')) is not int or value['id'] != identity:
            raise EmailProviderError('reconcile')
        return value


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
    if audio is not None:
        text += f'<p><a href="{P.SITE+audio["url"]}">'+('收听本期语音' if zh else 'Listen to this edition')+'</a></p>'
    else:
        text += '<p>'+('本期语音暂不可用，先送达已核验的文字简报。' if zh else 'Audio is temporarily unavailable. This verified text edition is ready to read.')+'</p>'
    text += f'<p><a href="{P.SITE}#/news">'+('查看网页与来源' if zh else 'Read the dashboard and sources')+'</a></p>'
    notice = ('您已订阅 FX Dashboard 的工作日简报。' if zh else 'You subscribed to FX Dashboard weekday briefings.')
    if audio is not None:
        notice += ('语音为合成语音。' if zh else ' Audio uses a synthetic voice.')
    text += '<p>'+notice+'</p>'
    text += f'<p>{escape(settings["sender_footer"])}</p>'
    text += '<p><a href="{{ unsubscribe }}">'+('退订' if zh else 'Unsubscribe')+'</a></p>'
    return {'name':f'fx-{brief["date"]}-{lang}-{brief["edition_hash"][:12]}',
            'sender':{'id':settings['sender_id']}, 'subject':title,'htmlContent':text,
            'recipients':{'listIds':[settings['lists'][lang]]}, 'mirrorActive':False}


def delivery_audio(settings, expected, verified, moment):
    """Select only proven attachments; a text fallback cannot excuse a mismatch."""
    try:
        age = (moment-datetime.fromisoformat(verified['observed_at'])).total_seconds()
        audio = verified['audio_verified']
        checks = verified.get('checks', [])
        if (verified.get('text_verified') is not True or verified.get('site') != P.SITE
                or any(verified.get(k) != expected[k] for k in ('date', 'mode', 'edition_hash'))
                or verified['expectation_hash'] != M.digest(expected) or not 0 <= age <= 900
                or not isinstance(audio, list) or len(audio) != len(set(audio))
                or set(audio)-{'en', 'zh'} or not isinstance(checks, list)
                or expected.get('media_errors')):
            raise ValueError()
        policy = delivery_policy(settings)
        if policy == 'require_audio':
            if verified['state'] != 'verified' or set(audio) != {'en', 'zh'} or checks:
                raise ValueError()
        elif policy == 'allow_text':
            allowed = {lang + suffix for lang in ('en', 'zh')
                       for suffix in ('_audio_missing', '_audio_probe_unavailable')}
            if verified['state'] not in {'verified', 'text_verified', 'pending'} or set(checks)-allowed:
                raise ValueError()
            for lang in {'en','zh'}-set(audio):
                reason = '_audio_probe_unavailable' if lang in expected['media'] else '_audio_missing'
                if lang+reason not in checks:
                    raise ValueError()
            if verified['state'] == 'verified' and (set(audio) != {'en','zh'} or checks):
                raise ValueError()
        else:
            raise ValueError()
        selected = {lang: expected['media'][lang] if lang in audio else None for lang in ('en', 'zh')}
        if any(selected[lang] is not None and lang+'_audio_probe_unavailable' in checks for lang in selected):
            raise ValueError()
        return selected
    except (KeyError, TypeError, ValueError):
        raise ValueError('public_delivery_unconfirmed') from None


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
        selected = delivery_audio(settings, expected, verified, moment)
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
                message = payload(settings,expected,selected[lang],lang)
                row = {'date':brief['date'],'language':lang,'edition_hash':brief['edition_hash'],
                       'payload_hash':M.digest(message),'state':'creating', 'started_at':moment.isoformat(),
                       'delivery_policy':delivery_policy(settings),
                       'content_mode':'audio' if selected[lang] is not None else 'text_only'}
                M.atomic_json(path,row)
                try:
                    identity = provider.create(message)
                    if type(identity) is not int or identity <= 0:
                        raise EmailProviderError('create', uncertain_outcome=True)
                    row.update(campaign_id=identity,state='submitting')
                    M.atomic_json(path,row)
                    provider.send(identity)
                    row.update(state='submitted',finished_at=clock().isoformat())
                except Exception as exc:
                    row.update(state='review_required',error_type=type(exc).__name__,finished_at=clock().isoformat())
                    if isinstance(exc, EmailProviderError):
                        row['error'] = exc.diagnostic()
                M.atomic_json(path,row)
                states[lang] = row['state']
        except M.Busy:
            states[lang] = 'busy'
    return {'state':'submitted' if set(states.values())=={'submitted'} else 'attention_required',
            'languages':states,'scope':'provider_submission_not_inbox_receipt'}


def provider_observation(value, moment):
    """Keep campaign totals and timestamps, never recipients, HTML or raw errors."""
    status = value.get('status')
    status = status if isinstance(status, str) and status in PROVIDER_STATUSES else None
    statistics = value.get('statistics')
    totals = statistics.get('globalStats') if isinstance(statistics, dict) else None
    totals = totals if isinstance(totals, dict) else {}
    stats = {k: v for k, v in totals.items() if k in PROVIDER_COUNTS and type(v) is int and 0 <= v <= 10**12}
    if isinstance(statistics, dict) and type(statistics.get('remaining')) is int and 0 <= statistics['remaining'] <= 10**12:
        stats['remaining'] = statistics['remaining']
    times = {}
    for key in PROVIDER_TIMES:
        try:
            stamp = datetime.fromisoformat(value[key])
            if not stamp.tzinfo:
                continue
            times[key] = stamp.astimezone(timezone.utc).isoformat()
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
    state = ('provider_confirmed' if stats.get('delivered', 0) > 0 else
             'provider_sent' if status == 'sent' or stats.get('sent', 0) > 0 else
             'not_confirmed' if status is not None else 'unreadable')
    return {'state':state, 'provider_status':status, 'observed_at':moment.isoformat(),
            'stats':stats, 'times':times}


def reconcile(output_dir, day, lang, *, provider_factory=None, clock=M.now_utc):
    """Read a known campaign with GET; leave every daily delivery claim intact."""
    try:
        valid = isinstance(day, str) and date.fromisoformat(day).isoformat() == day and lang in {'en', 'zh'}
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('invalid_email_reconciliation_identity')
    root = Path(output_dir)/'subscriptions'
    path = root/'deliveries'/day/(lang+'.json')
    receipt = M.read_json(path)
    if not path.exists():
        return {'state':'no_delivery_claim','date':day,'language':lang,'network_called':False}
    if (receipt.get('date') != day or receipt.get('language') != lang
            or not all(isinstance(receipt.get(k), str) and B.HASH.fullmatch(receipt[k])
                       for k in ('edition_hash','payload_hash'))
            or not isinstance(receipt.get('state'), str)
            or receipt['state'] not in {'creating','submitting','submitted','review_required'}):
        return {'state':'manual_review_required','date':day,'language':lang,'network_called':False}
    identity = receipt.get('campaign_id')
    if type(identity) is not int or identity <= 0:
        return {'state':'manual_review_required','date':day,'language':lang,
                'claim_state':receipt.get('state'),'network_called':False}
    row = {k:receipt[k] for k in ('date','language','edition_hash','payload_hash','campaign_id')}
    row.update(schema_version=1, claim_state=receipt.get('state'), scope='provider_report_not_inbox_receipt')
    try:
        provider = (provider_factory or Provider)()
    except Exception:
        return {**row,'state':'configuration_required','network_called':False}
    try:
        report = provider.report(identity)
        if not isinstance(report, dict) or type(report.get('id')) is not int or report['id'] != identity:
            raise EmailProviderError('reconcile')
        row.update(state='observed',network_called=True,provider_observation=provider_observation(report,clock()))
        M.atomic_json(root/'provider-observations'/day/(lang+'.json'),row)
        return row
    except Exception as exc:
        result = {**row,'state':'reconciliation_unavailable','network_called':True,'error_type':type(exc).__name__}
        if isinstance(exc, EmailProviderError):
            result['error'] = exc.diagnostic()
        return result


def main(argv=None):
    import argparse
    import sys
    from ..config import OUTPUT_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check',action='store_true')
    group.add_argument('--configure',action='store_true',help='Read operator settings, without keys, from stdin')
    group.add_argument('--disable',action='store_true')
    group.add_argument('--reconcile',action='store_true',help='GET the recorded campaign report; never resend a claim')
    group.add_argument('--delivery-policy',choices=sorted(POLICIES),help='Update an existing valid configuration without sending')
    parser.add_argument('--date')
    parser.add_argument('--lang',choices=('en','zh'))
    args = parser.parse_args(argv)
    if args.reconcile:
        if args.date is None or args.lang is None:
            parser.error('--reconcile requires --date and --lang')
        result = reconcile(OUTPUT_DIR,args.date,args.lang)
        print(json.dumps(result,ensure_ascii=True))
        return 0 if result['state'] == 'observed' else 2
    if args.date is not None or args.lang is not None:
        parser.error('--date and --lang require --reconcile')
    if args.delivery_policy:
        value = config(OUTPUT_DIR)
        if value is None:
            raise ValueError('valid_email_settings_required')
        value['delivery_policy'] = args.delivery_policy
        M.atomic_json(OUTPUT_DIR/'subscriptions/config.json',value)
    if args.disable:
        value = M.read_json(OUTPUT_DIR/'subscriptions/config.json')
        value['enabled'] = False
        M.atomic_json(OUTPUT_DIR/'subscriptions/config.json',value)
    if args.configure:
        value = json.loads(sys.stdin.read(12000))
        allowed = {'enabled','double_opt_in_confirmed','quota_approved','sender_id','sender_footer','forms','lists','delivery_policy'}
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
    result = {'enabled':bool(config(OUTPUT_DIR)),'key_format_valid':key_ok,'network_called':False}
    if args.delivery_policy:
        result['delivery_policy'] = args.delivery_policy
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
