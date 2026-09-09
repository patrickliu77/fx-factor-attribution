"""One synthetic API smoke test; never reads news or writes a briefing."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def main():
    from fxdash.narrative.client import GeminiClient, failure_details
    from fxdash.narrative.driver_notes import SYSTEM, SCHEMA
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-request',action='store_true')
    args=parser.parse_args()
    if not args.allow_request:
        parser.error('Pass --allow-request for one model request using synthetic input.')
    client=GeminiClient(timeout=30,max_requests=1,max_attempts=1)
    payload={'pair':'USDCAD','attribution_date':'2026-01-07',
             'leading':[{'factor':'WTI'}],'sources':[],
             'instruction':'No source evidence is available. Return insufficient_evidence with empty prose.'}
    result={'synthetic_probe':True,'briefing_written':False,'model':client.model}
    try:
        response=client.complete(SYSTEM,json.dumps(payload),SCHEMA)
        result.update(state='completed',assessment=response.get('assessment'),
                      structure_matches=isinstance(response,dict) and set(response)==set(SCHEMA['properties']))
    except Exception as exc:
        result.update(state='failed',failure=failure_details(exc))
    result['usage']=client.totals
    print(json.dumps(result))
    return 0 if result['state']=='completed' and result['structure_matches'] else 1


if __name__=='__main__':raise SystemExit(main())
