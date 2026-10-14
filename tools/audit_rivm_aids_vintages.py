"""Read-only PDF extraction and vintage audit; never changes production targets."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LITERATURE = ROOT.parent / 'literature'
SOURCES = {
    'D76': ('RIVM(2016)-STIandHIVinNL2015.pdf', 2016, 2015, [111, 112], 6),
    'D77': ('RIVM(2017)-STIandHIVinNL2016.pdf', 2017, 2016, [118], 4),
    'D78': ('RIVM(2023)-STIandHIVinNL2022.pdf', 2023, 2022, [128], 4),
    'D79': ('RIVM(2024)-STIandHIVinNL2023.pdf', 2024, 2023, [129], 4),
}


def extract():
    result = {}
    for source, (filename, vintage, incomplete, pages, n_groups) in SOURCES.items():
        path = LITERATURE / filename
        text = subprocess.check_output([
            'pdftotext', '-layout', '-f', str(pages[0]), '-l', str(pages[-1]), str(path), '-'
        ], text=True)
        heading = 'Table 6.11 Number of AIDS patients' if source == 'D76' else 'Table 6.9 Number of AIDS patients'
        text = text.split(heading, 1)[1]
        if source != 'D76':
            text = text.split('Source', 1)[0]  # excludes the following death table
        rows = []
        for line in text.splitlines():
            match = re.match(r'^\s*(≤\s*\d{4}|\d{4}|Total)\s+', line)
            if not match:
                continue
            pairs = re.findall(r'([\d,]+)\s*\(([\d.]+)\)', line)
            if not pairs:
                continue
            if len(pairs) != n_groups:
                raise ValueError(f'{source}: unexpected group count: {line}')
            period = match[1].replace(' ', '')
            counts = [int(n.replace(',', '')) for n, _ in pairs]
            percentages = [float(p) for _, p in pairs]
            total = sum(counts)
            names = (['heterosexual_contact', 'msm', 'injecting_drug_use', 'blood_contacts', 'mother_to_child', 'other_unknown']
                     if source == 'D76' else ['heterosexual_women' if source == 'D77' else 'women', 'heterosexual_men', 'msm', 'other_unknown_combined'])
            percentage_flags = [names[i] for i, (n, pct) in enumerate(zip(counts, percentages))
                                if abs(100*n/total-pct) > 0.051]
            rows.append({'period': period, 'counts': dict(zip(names, counts)),
                         'reported_percentages': dict(zip(names, percentages)),
                         'total_derived_from_groups': total,
                         'incomplete': period == str(incomplete),
                         'percentage_consistency_flags': percentage_flags,
                         'pooled_or_total': not period.isdigit()})
        assert len(rows) == (30 if source == 'D76' else 12 if source == 'D77' else 10), (source, len(rows))
        result[source] = {'file': filename, 'publication_year': vintage, 'pdf_pages': pages,
                          'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'rows': rows}
    return result


def main():
    sources = extract()
    annual = {s: {int(r['period']): r for r in v['rows'] if r['period'].isdigit()}
              for s, v in sources.items()}
    lines = ['# RIVM AIDS source-vintage audit', '',
             'Source extraction only. No production targets or presentation fractions changed.', '',
             'Annual totals below are sums of the reported transmission-group counts.', '',
             '| AIDS diagnosis year | D76 (2016) | D77 (2017) | D78 (2023) | D79 (2024) |',
             '|---|---:|---:|---:|---:|']
    for year in range(2007, 2024):
        cells = []
        for source in sources:
            r = annual[source].get(year)
            cells.append('—' if r is None else str(r['total_derived_from_groups']) + ('*' if r['incomplete'] else ''))
        lines.append(f'| {year} | ' + ' | '.join(cells) + ' |')
    lines += ['', '*Asterisk denotes the report’s explicitly incomplete terminal year.*', '',
              '## Source caveats', '',
              '- D77 Table 6.9: the pooled ≤2006 row sums to 22,651, versus D76 cumulative AIDS diagnoses through 2006 of 7,422. Its printed grand total is 25,040. Quarantine the pooled and grand-total rows; no cause of this inconsistency is established. Later annual rows require their own review and are not automatically invalid.',
              '- D79 Table 6.9: 2016 counts sum to 218; its MSM count of 100 implies 45.87%, not the printed 46.9%. Preserve both as printed and flag the inconsistency. Do not silently repair the count or percentage.',
              '- D76 has six routes. Later editions combine injecting drug use, blood contacts, mother-to-child, other and unknown. D77 labels women heterosexual; D78/D79 label women without that qualifier. These categories need population-eligibility review before mapping to model orientations.',
              '- All editions describe AIDS by year of AIDS diagnosis, not solely AIDS at HIV presentation. Neither late-presentation figures nor AIDS/HIV annual ratios provide the required conditional presentation fraction.',
              '- Differences between editions demonstrate revision uncertainty. They do not, on their own, estimate an observation-error variance or prove every revision is delayed reporting.', '',
              '## Proposed use, not activated', '',
              'Retain D76 unchanged as the selected historical anchor. Use D78/D79 as a separately versioned recent-series candidate and overlap sensitivity. Prefer mature observations to explicitly incomplete terminal years, while recognising that older years also continue to be revised. Avoid silently splicing or averaging overlapping editions.', '',
              'A possible extension is D79 2015–2022 alongside D76 through 2014, with overlap results reported separately. This preserves the requested historical anchor but introduces a source-vintage seam; it is a proposal, not an approved production series. An alternative latest-vintage-by-year series would revise the historical overlap as well and requires an explicit source-policy decision.', '',
              'Before scoring: align clinical/reporting events, adult/group eligibility, migrant history and the process-versus-Monte-Carlo variance contract. Do not use the source switch to force retention of a particular old parameter vector.', '',
              '## Provenance', '']
    for source, data in sources.items():
        lines.append(f"- {source}: `{data['file']}`, PDF pages {data['pdf_pages']}, SHA256 `{data['sha256']}`.")
    out = ROOT / 'outputs/calibration/rivm_aids_vintage_audit_20260916'
    out.mkdir(parents=True, exist_ok=True)
    (out/'source_extraction.json').write_text(json.dumps(sources, indent=2)+'\n')
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines[:26]))
    print('Saved:', out)


if __name__ == '__main__':
    main()
