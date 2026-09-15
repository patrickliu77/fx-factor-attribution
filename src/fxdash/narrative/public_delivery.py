"""Read-only public delivery checks, separate from a successful Git push.

Only fixed project URLs are fetched. No redirects, provider calls or publication.
Each scheduled invocation makes one bounded probe; later invocations handle CDN lag.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from . import morning as M, briefing_archive as A, audio_briefing as B

SITE = 'https://patrickliu77.github.io/fx-factor-attribution/'
COOLDOWN_SECONDS = 300


def fetch(relative, limit):
    import requests
    if relative not in {'build.json', 'api/news.json'} and not re.fullmatch(
            r'media/briefing/(edition|catchup)/\d{4}-\d{2}-\d{2}/[0-9a-f]{64}/audio-v[12345]/(en|zh)\.mp3', relative):
        raise ValueError('unapproved_public_asset')
    with requests.get(SITE+relative, timeout=(5, 12), allow_redirects=False, stream=True,
                      headers={'Cache-Control':'no-cache'}) as response:
        if response.status_code != 200:
            raise ValueError('public_asset_unavailable')
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > limit:
                raise ValueError('public_asset_too_large')
            chunks.append(chunk)
        return b''.join(chunks)


def target(output_dir, brief):
    # Validate date, mode and hash before constructing any output path.
    B.sidecar(output_dir, brief)
    return Path(output_dir)/'delivery'/brief['mode']/brief['date']/brief['edition_hash'][:20]/'public.json'


def expectation(output_dir, brief):
    saved = A.read_edition(B.edition_path(output_dir, brief['mode'], brief['date']), mode=brief['mode'])
    if saved.get('edition_hash') != brief.get('edition_hash') or saved.get('state') not in {'ready','numbers_only'}:
        raise ValueError('public_check_source_unavailable')
    audio = B.inspect(output_dir, saved)
    media = {lang: {k: item[k] for k in ('url','audio_sha256','script_sha256')}
             for lang, item in audio['languages'].items() if item.get('state') == 'ready'}
    return {'date':saved['date'], 'mode':saved['mode'], 'edition_hash':saved['edition_hash'],
            'text':saved['text'], 'attribution_as_of':saved['attribution_as_of'],
            'calendar':saved.get('calendar'), 'recap':saved.get('recap'), 'media':media}


def verify(output_dir, brief, *, fetcher=None, clock=M.now_utc, force=False):
    """Text and MP3 bytes must match the local frozen edition. Never infer delivery."""
    expected = expectation(output_dir, brief)
    path, moment = target(output_dir, brief), clock()
    digest = M.digest(expected)
    try:
        with M.DayLock(path.with_suffix('.lock')):
            previous = M.read_json(path)
            if previous.get('expectation_hash') == digest and not force:
                try:
                    age = (moment-datetime.fromisoformat(previous['observed_at'])).total_seconds()
                    if 0 <= age < COOLDOWN_SECONDS:
                        return previous
                except (KeyError, ValueError, TypeError):
                    pass
            result = {'schema_version':1, 'date':brief['date'], 'mode':brief['mode'],
                      'edition_hash':brief['edition_hash'], 'expectation_hash':digest,
                      'site':SITE, 'observed_at':moment.isoformat(), 'state':'pending',
                      'text_verified':False, 'audio_verified':[], 'checks':[]}
            get = fetcher or fetch
            try:
                build = json.loads(get('build.json', 1_000_000))
                identity = build.get('briefing') or {}
                if any(identity.get(k, 'edition' if k == 'mode' else None) != expected[k]
                       for k in ('date','mode','edition_hash')):
                    result['checks'].append('public_build_edition_mismatch')
                else:
                    payload = json.loads(get('api/news.json', 8_000_000))
                    current = payload.get('briefing') or {}
                    if any(current.get(k) != expected[k] for k in
                           ('date','mode','edition_hash','text','attribution_as_of','calendar','recap')):
                        result['checks'].append('public_text_mismatch')
                    else:
                        result['text_verified'] = True
                        remote_media = (current.get('audio') or {}).get('languages') or {}
                        for lang, item in expected['media'].items():
                            remote = remote_media.get(lang) or {}
                            if remote.get('state') != 'ready' or any(remote.get(k) != v for k,v in item.items()):
                                result['checks'].append(lang+'_audio_manifest_mismatch')
                            elif hashlib.sha256(get(item['url'], 5_000_000)).hexdigest() != item['audio_sha256']:
                                result['checks'].append(lang+'_audio_bytes_mismatch')
                            else:
                                result['audio_verified'].append(lang)
                        if not result['checks']:
                            result['state'] = 'verified' if len(expected['media']) == 2 else 'text_verified'
                result['public_built_at'] = build.get('built_at')
            except Exception as exc:
                # Provider bodies, URLs with query parameters and raw errors stay out.
                result['checks'].append('public_probe_unavailable')
                result['error_type'] = type(exc).__name__
            M.atomic_json(path, result)
            stamp = moment.strftime('%Y%m%dT%H%M%S%f')
            M.atomic_json(path.parent/'checks'/(stamp+'.json'), result)
            return result
    except M.Busy:
        return {'state':'busy'}


def observation(output_dir, brief, *, clock=M.now_utc):
    """Validate a saved probe without fetching or asserting current availability."""
    try:
        path = target(output_dir,brief)
        if not path.exists():
            return {'state':'not_checked'}
        value = M.read_json(path)
        observed = datetime.fromisoformat(value['observed_at'])
        expected = expectation(output_dir,brief)
        if (not observed.tzinfo or observed>clock() or value.get('expectation_hash')!=M.digest(expected)
                or value.get('edition_hash')!=brief['edition_hash'] or value.get('site')!=SITE
                or value.get('state') not in {'pending','verified','text_verified'}):
            raise ValueError('public_receipt_mismatch')
        return {k:value[k] for k in ('state','observed_at','text_verified','audio_verified')}
    except (KeyError,ValueError,TypeError,OSError):
        return {'state':'unconfirmed'}


def main(argv=None):
    from ..config import OUTPUT_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=OUTPUT_DIR)
    args = parser.parse_args(argv)
    brief = A.dashboard(args.output_dir)['current']
    if brief.get('state') not in {'ready','numbers_only'}:
        print(json.dumps({'state':'no_saved_edition'}))
        return 2
    result = verify(args.output_dir, brief, force=True)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result['state'] == 'verified' else 2


if __name__ == '__main__':
    raise SystemExit(main())
