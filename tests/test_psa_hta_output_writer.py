import pandas as pd
from openpyxl import load_workbook

from psa_hta_output_writer import PSAHTAOutputWriter


class _MockSampler:
    def get_parameter_summary(self):
        return pd.DataFrame([
            {"path": "mock.parameter", "dist_type": "fixed", "value": 1.0}
        ])


def test_psa_hta_summary_embeds_generated_figures(tmp_path):
    incremental_df = pd.DataFrame([
        {
            "draw_index": i,
            "comparison": "lenacapavir_vs_oral_prep",
            "delta_cost": 1_000_000 + i * 100_000,
            "delta_qaly": 30 + i,
            "icer": 30_000 + i * 1_000,
            "nmb": 80_000 * (30 + i) - (1_000_000 + i * 100_000),
            "infections_averted": 10 + i,
        }
        for i in range(4)
    ])
    arm_outcomes_df = pd.DataFrame([
        {
            "draw_index": i,
            "arm": arm,
            "total_qalys_disc": 1_000 + i,
            "total_costs_disc": 10_000 + i,
            "total_costs_disc_societal": 12_000 + i,
            "cumul_new_infections": 100 - i,
        }
        for i in range(4)
        for arm in ["oral_prep", "lenacapavir"]
    ])

    writer = PSAHTAOutputWriter(
        incremental_df=incremental_df,
        arm_outcomes_df=arm_outcomes_df,
        epi_sampler=_MockSampler(),
        hta_sampler=_MockSampler(),
        psa_config={},
        hta_config={"wtp_threshold_eur_per_qaly": {"value": 80_000}},
        output_dir=tmp_path,
    )

    writer.write_all()

    workbook_path = tmp_path / "psa_hta_summary.xlsx"
    wb = load_workbook(workbook_path)

    assert "PSA Figures" in wb.sheetnames
    assert "ceac_data" in wb.sheetnames
    assert "raw_incremental" in wb.sheetnames
    assert len(wb["PSA Figures"]._images) >= 2
