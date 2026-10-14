"""Build a provenance-preserving evidence candidate, never an active HM target."""
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_candidate(sources):
    selected = {}
    excluded = []
    for source, data in sorted(sources.items(), key=lambda item: item[1]['publication_year']):
        for row in data['rows']:
            period = row['period']
            reason = None
            if not period.isdigit():
                reason = 'pooled_or_total_not_annual'
            elif source == 'D77':
                reason = 'annual_review_pending_after_source_inconsistency'
            elif int(period) == 2016:
                reason = 'year_held_pending_D79_count_percentage_reconciliation'
            elif row['incomplete']:
                reason = 'explicitly_incomplete'
            elif row['percentage_consistency_flags']:
                reason = 'count_percentage_inconsistency'
            record = {**copy.deepcopy(row), 'source_id': source,
                      'source_file': data['file'], 'source_sha256': data['sha256'],
                      'pdf_pages': data['pdf_pages'], 'publication_year': data['publication_year']}
            if reason:
                excluded.append({**record, 'exclusion_reason': reason})
                continue
            year = int(period)
            if year in selected:
                excluded.append({**selected[year], 'exclusion_reason': 'superseded_vintage_retained_for_sensitivity'})
            selected[year] = record
    return {'status': 'EVIDENCE_CANDIDATE_NOT_ACTIVE_TARGET',
            'scope': 'Annual group-table records only; 1983–1987 annual totals remain in the D76 workbook.',
            'selected': [selected[y] for y in sorted(selected)], 'excluded': excluded}


def main():
    source = ROOT / 'outputs/calibration/rivm_aids_vintage_audit_20260916/source_extraction.json'
    policy = ROOT / 'configs/rivm_aids_source_policy_20260916.json'
    candidate = build_candidate(json.loads(source.read_text()))
    candidate['extraction_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    candidate['policy'] = json.loads(policy.read_text())
    candidate['policy_sha256'] = hashlib.sha256(policy.read_bytes()).hexdigest()
    output = source.parent / 'candidate_newest_vetted_20260916.json'
    output.write_text(json.dumps(candidate, indent=2) + '\n')
    print(f"Saved {len(candidate['selected'])} candidate annual rows: {output}")
    print('Not run-ready: see policy run_blockers. No production targets changed.')


if __name__ == '__main__':
    main()
