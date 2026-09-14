import copy
from datetime import datetime

import pytest

from fxdash.narrative import release_calendar as C, morning as M, briefing_archive as A
from test_morning import moment
from test_briefing_archive import edition, save

attach_real=C.attach


def feed(stamp='20260108T133000Z', extra='', title='Consumer Price Index', params=''):
    return f'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:test-1\r\nSUMMARY:{title}\r\nDTSTART{params}:{stamp}\r\n{extra}END:VEVENT\r\nEND:VCALENDAR'


def test_unfolds_crcrlf_and_reads_utc():
    rows,skipped=C.parse_ics(feed(title='Gross Domestic Product\\,\r\n first estimate').replace('\r\n','\r\r\n'),'BEA')
    assert skipped==0 and rows[0]['title']=='Gross Domestic Product,first estimate'
    assert rows[0]['scheduled_at']=='2026-01-08T13:30:00+00:00'


@pytest.mark.parametrize('stamp,utc', [('20260309T083000','2026-03-09T12:30:00+00:00'),('20261102T083000','2026-11-02T13:30:00+00:00')])
def test_explicit_eastern_zone_handles_dst(stamp,utc):
    rows,_=C.parse_ics(feed(stamp,params=';TZID=America/New_York'),'BLS')
    assert rows[0]['scheduled_at']==utc


@pytest.mark.parametrize('text',[feed('20260108'),feed('20260108T083000'),feed(extra='RRULE:FREQ=DAILY\r\n')])
def test_unknown_time_or_recurrence_is_never_guessed(text):
    rows,skipped=C.parse_ics(text,'BLS')
    assert rows==[] and skipped==1


def test_cancelled_and_conflicting_events_are_removed():
    assert C.parse_ics(feed(extra='STATUS:CANCELLED\r\n'),'BEA')[0]==[]
    first=feed()
    second=feed(title='Conflicting title').split('BEGIN:VCALENDAR\r\n')[1]
    text=first.replace('END:VCALENDAR','')+second
    rows,skipped=C.parse_ics(text,'BEA')
    assert rows==[] and skipped==1


def test_partial_source_failure_retains_observation_without_faking_coverage(tmp_path):
    calls=[]
    def get(url):
        calls.append(url)
        if url==C.SOURCES['BLS']: raise ValueError('secret detail')
        return feed()
    result=C.capture(tmp_path,clock=lambda:moment(13,50),fetcher=get)
    assert result['state']=='partial' and len(result['events'])==1
    assert 'secret' not in str(result) and result['sources'][0]['state']=='unavailable'
    source=result['sources'][1]
    import hashlib
    assert hashlib.sha256((tmp_path/source['input_file']).read_bytes()).hexdigest()==source['content_sha256']
    assert C.capture(tmp_path,clock=lambda:moment(13,51),fetcher=lambda *a:pytest.fail('cache'))==result


def test_all_failed_is_unavailable_not_no_events(tmp_path):
    def get(url): raise ValueError('unavailable')
    result=C.capture(tmp_path,clock=lambda:moment(13,50),fetcher=get)
    assert result['state']=='unavailable' and result['events']==[]


def test_old_feed_is_coverage_expired(tmp_path):
    result=C.capture(tmp_path,clock=lambda:moment(13,50),fetcher=lambda u:feed('20200108T133000Z'))
    assert all(s['state']=='coverage_expired' for s in result['sources'])


def test_context_is_frozen_and_cannot_gain_future_observations(tmp_path):
    value=edition(tmp_path)
    value['evidence']['calendar']=C.capture(tmp_path,clock=lambda:moment(13,50),fetcher=lambda u:feed())
    path=save(tmp_path,value)
    before=path.read_bytes()
    context=A.read_edition(path)['calendar']
    assert context['events'] and path.read_bytes()==before
    packet=copy.deepcopy(value['evidence'])
    packet['calendar']['observed_at']=moment(15,0).isoformat()
    assert C.public_context(packet,value['generated_at']) is None


def test_attach_does_not_hide_a_late_fetch(tmp_path,monkeypatch):
    monkeypatch.setattr(C,'capture',lambda *a,**kw:{'state':'unavailable'})
    packet={'fetched_at':moment(13,55).isoformat()}
    attach_real(packet,tmp_path,clock=lambda:moment(14,1))
    assert packet['fetched_at']==moment(14,1).isoformat(timespec='seconds')


def test_new_neural_script_uses_dated_calendar_without_rewriting_old_copy(tmp_path):
    from test_audio_briefing import saved
    from fxdash.narrative import audio_script as S
    path,edition,packet=saved(tmp_path)
    original=S.compose(edition,packet,'en',version=S.NEURAL_VERSION)
    context=C.capture(tmp_path,clock=lambda:moment(16,0),fetcher=lambda u:feed('20260109T133000Z'))
    packet['calendar']=context
    edition['evidence']=packet
    edition['packet_hash']=M.digest(packet)
    text=S.compose(edition,packet,'en',version=S.NEURAL_VERSION)
    assert 'US calendar' in text and 'January 9' in text
    assert 'there is no economic calendar' not in text
    assert 'there is no economic calendar' in original
    assert 'calendar' not in M.read_json(path)['evidence']
