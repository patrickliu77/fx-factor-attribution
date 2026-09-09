"""Save/check frozen-row fingerprints around a normal live recovery. No fitting."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def main():
    import pandas as pd
    from fxdash.attribution.contract import read_contract
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['before','after'])
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    args.directory.mkdir(parents=True,exist_ok=True)
    keys=['date','pair','window','model']
    frame=read_contract().sort_values(keys).reset_index(drop=True)
    frame=frame.reindex(sorted(frame.columns),axis=1)
    records=frame[keys].copy()
    records['fingerprint']=pd.util.hash_pandas_object(frame,index=False).to_numpy()
    baseline=args.directory/'frozen-before.parquet'
    if args.action=='before':
        if baseline.exists():raise ValueError('baseline_already_exists')
        records.loc[~frame.provisional].to_parquet(baseline,index=False)
        print(json.dumps({'frozen_rows':int((~frame.provisional).sum()),'total_rows':len(frame),
                          'latest_date':str(frame.date.max().date())}))
    else:
        old=pd.read_parquet(baseline)
        joined=old.merge(records,on=keys,how='left',suffixes=('_before','_after'),validate='one_to_one')
        # Preserve uint64 precision during the join, including missing-row checks.
        missing=joined.fingerprint_after.isna()
        same=not missing.any() and (joined.fingerprint_before==joined.fingerprint_after).all()
        result={'frozen_rows_checked':len(old),'missing_rows':int(missing.sum()),
                'unchanged':bool(same),'total_rows':len(frame),'latest_date':str(frame.date.max().date())}
        print(json.dumps(result))
        if not same:raise ValueError('frozen_history_changed')


if __name__=='__main__':main()
