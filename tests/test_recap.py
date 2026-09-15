import copy
import math

import pytest

from fxdash.narrative import recap as R, audio_script as S, morning as M, briefing_archive as A, subscriptions as E
from test_audio_briefing import saved


def inputs(root, returns, residuals=None):
    path, edition, packet = saved(root, commentary=False)
    for row, value in zip(packet['pairs'], returns):
        row.update(y=value, residual=0)
    if residuals:
        for row, value in zip(packet['pairs'], residuals):
            row['residual'] = value
    edition['evidence'] = packet
    edition['packet_hash'] = M.digest(packet)
    return path, edition, packet


@pytest.mark.parametrize('returns,tone', [
    ([.009,.002,.003,-.0001,.0056,.0083], 'up'),
    ([-.009,-.002,-.003,.0001,-.0056,-.0083], 'down'),
    ([.002,-.002,.003,-.003,.004,-.004], 'mixed'),
    ([0,.00001,-.00001,0,0,0], 'quiet'),
    ([.002,0,0,0,0,0], 'some_up'),
    ([-.002,0,0,0,0,0], 'some_down'),
])
def test_overall_direction_accounts_for_all_six_pairs(tmp_path, returns, tone):
    _, edition, packet = inputs(tmp_path, returns)
    assert R.direction(packet['pairs']) == tone
    for lang in ('en','zh'):
        text = S.compose(edition, packet, lang, version=S.RECAP_VERSION)
        assert ('basis points' if lang=='en' else '基点') not in text
        assert ('provisional' if lang=='en' else '待确认') not in text
        assert ('synthetic voice' if lang=='en' else '合成语音') not in text
        assert text.count('percent' if lang=='en' else '%') <= 2
        assert not any(c in text for c in '—–')


def test_uses_simple_percent_returns_with_correct_usd_quote_direction(tmp_path):
    _, edition, packet = inputs(tmp_path, [math.log(1.01), math.log(.98), 0, 0, 0, 0])
    assert R.price_change(packet['pairs'][0]) == pytest.approx(1)
    assert R.price_change(packet['pairs'][1]) == pytest.approx(-2)
    text = S.compose(edition, packet, 'en', version=S.RECAP_VERSION)
    assert 'it rose about 1 percent' in text and 'it fell about 2 percent' in text
    assert 'broadly stronger' not in text and 'broadly weaker' not in text


def test_only_one_substantial_model_gap_is_mentioned_without_numbers(tmp_path):
    _, _, packet = inputs(tmp_path, [.009,.008,.007,0,0,0], [.005,.006,.004,.1,0,0])
    line = R.model_observation(packet['pairs'], 'en')
    assert S.PAIRS[packet['pairs'][1]['pair']][0] in line
    assert 'usual factors' in line and 'residual' not in line and not any(c.isdigit() for c in line)
    assert R.model_observation([{'pair':'USDJPY','y':.00001,'residual':.01}], 'en') is None
    assert R.model_observation([{'pair':'USDJPY','y':.009,'residual':.001}], 'en') is None


def test_frozen_source_is_unchanged_and_email_matches_the_spoken_recap(tmp_path):
    path, edition, packet = saved(tmp_path)
    originals = {p:p.read_bytes() for p in (path,path.parent/'packet.json')}
    published = A.read_edition(path, mode='catchup')
    assert published['text'] == edition['text']
    assert published['edition_hash'] == M.digest(edition)
    for lang in ('en','zh'):
        spoken = S.compose(edition, packet, lang, version=S.RECAP_VERSION)
        assert E.email_text(published, lang) == published['recap']['text'][lang] == spoken
        assert len(spoken) < len(S.compose(edition, packet, lang, version='audio-v4'))
    assert all(p.read_bytes()==v for p,v in originals.items())


def test_invalid_inputs_never_get_a_derived_recap(tmp_path):
    path, edition, _ = saved(tmp_path)
    edition['evidence']['pairs'][0]['y'] = .99
    assert 'recap' not in A.public_copy(edition)
    assert 'recap' in A.read_edition(path, mode='catchup')
    with pytest.raises(ValueError):
        R.price_change({'y':1e308})


def test_only_complete_short_verified_news_is_spoken(tmp_path, monkeypatch):
    _, edition, packet = saved(tmp_path)
    assert R.news(edition, packet, 'en')
    edited = copy.deepcopy(edition)
    edited['notes'][0]['note']['en']['event'] = 'The yen will rise tomorrow.'
    assert R.news(edited, packet, 'en') is None
    # The editorial length gate is independent of the evidence validator.
    monkeypatch.setattr('fxdash.narrative.driver_notes.validate', lambda *a: [])
    edited['notes'][0]['note']['en']['event'] = 'A complete attributed report. '*20
    assert R.news(edited, packet, 'en') is None
    edited['notes'][0]['note']['en']['event'] = 'The residual was 100 basis points.'
    assert R.news(edited, packet, 'en') is None


def test_calendar_uses_only_upcoming_dated_items_and_skips_empty_status(tmp_path, monkeypatch):
    _, edition, packet = saved(tmp_path)
    monkeypatch.setattr('fxdash.narrative.release_calendar.public_context', lambda *a: {'events': []})
    assert R.calendar(edition, packet, 'en') is None
    events = [
        {'id':'passed','title':'Passed event','scheduled_at':'2026-01-08T15:00:00Z'},
        {'id':'later','title':'Later event','scheduled_at':'2026-01-09T16:00:00Z'},
        {'id':'next','title':'Next event','scheduled_at':'2026-01-09T13:30:00Z'},
        {'id':'naive','title':'Undated timezone','scheduled_at':'2026-01-09T12:00:00'},
    ]
    monkeypatch.setattr('fxdash.narrative.release_calendar.public_context', lambda *a: {'events':events})
    text = R.calendar(edition, packet, 'en')
    assert 'Next event' in text and 'January 9' in text and '08:30 New York time' in text
    assert 'Later event' not in text and 'Passed event' not in text
