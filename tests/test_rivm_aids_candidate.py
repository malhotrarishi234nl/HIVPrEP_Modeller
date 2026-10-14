import copy

from tools.build_rivm_aids_candidate import build_candidate


def test_candidate_preserves_provenance_and_quarantines_uncertain_rows():
    sources = {}
    for source, vintage, years in [('D76', 2016, [1990, 2013, 2014, 2015]),
                                   ('D77', 2017, [2014, 2015, 2016]),
                                   ('D78', 2023, [2013, 2014, 2015, 2016, 2022]),
                                   ('D79', 2024, [2014, 2015, 2016, 2022, 2023])]:
        sources[source] = {'publication_year': vintage, 'file': source+'.pdf',
                           'sha256': 'test-hash', 'pdf_pages': [1], 'rows': [
            {'period': str(year), 'total_derived_from_groups': 255,
             'incomplete': year == vintage-1, 'percentage_consistency_flags':
             ['msm'] if source == 'D79' and year == 2016 else []}
            for year in years]}
    original = copy.deepcopy(sources)
    candidate = build_candidate(sources)
    rows = {int(r['period']): r for r in candidate['selected']}
    assert sources == original
    assert len(rows) == 5
    assert rows[1990]['source_id'] == 'D76'
    assert rows[2013]['source_id'] == 'D78'
    assert rows[2014]['source_id'] == 'D79'
    assert rows[2015]['total_derived_from_groups'] == 255
    assert 2016 not in rows and 2023 not in rows
    assert all(r['source_sha256'] and not r['incomplete'] for r in rows.values())
    assert all(r['source_id'] != 'D77' for r in rows.values())
    assert any(r['source_id'] == 'D79' and r['period'] == '2016' for r in candidate['excluded'])
