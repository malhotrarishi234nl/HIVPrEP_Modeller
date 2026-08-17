"""
psa_hta_output_writer.py — CE plane, CEAC, and summary outputs for PSA × HTA.

Writes:
  Raw CSVs:
    psa_hta_incremental.csv     — one row per (draw × comparison)
    psa_hta_arm_outcomes.csv    — one row per (draw × arm)
  Summary Excel:
    psa_hta_summary.xlsx        — 6 sheets (see _write_summary_excel)
  Plots (PNG):
    ce_plane_{comparison}.png   — scatter of (ΔE, ΔC), WTP threshold line
    ceac.png                    — P(CE) vs WTP for all comparisons
    icer_histogram_{comparison}.png
    convergence_icer_{comparison}.png

V11.11.4 — 2026-04-29
"""

import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path


class PSAHTAOutputWriter:
    """
    Writes PSA × HTA outputs.

    Parameters
    ----------
    incremental_df   : pd.DataFrame  shape (N*C, cols)
    arm_outcomes_df  : pd.DataFrame  shape (N*3, cols)
    epi_sampler      : PSASampler
    hta_sampler      : HTASampler
    psa_config       : dict
    hta_config       : dict
    output_dir       : Path
    failed_draws     : list          (optional)
    """

    WTP_RANGE = list(range(0, 155_000, 5_000))  # 0..150,000 in steps of 5,000

    def __init__(self, incremental_df, arm_outcomes_df,
                 epi_sampler, hta_sampler, psa_config, hta_config,
                 output_dir, failed_draws=None):
        self.incremental_df = incremental_df
        self.arm_outcomes_df = arm_outcomes_df
        self.epi_sampler = epi_sampler
        self.hta_sampler = hta_sampler
        self.psa_config = psa_config
        self.hta_config = hta_config
        self.output_dir = Path(output_dir)
        self.failed_draws = failed_draws or []

        # Configured WTP (used for the WTP line on CE plane and NMB column)
        from config_utils import extract_value
        self.wtp = extract_value(hta_config["wtp_threshold_eur_per_qaly"])

        # Ensure configured WTP appears in WTP range for CEAC
        if self.wtp not in self.WTP_RANGE:
            self.WTP_RANGE = sorted(set(self.WTP_RANGE) | {int(self.wtp)})

        self.comparisons = sorted(self.incremental_df["comparison"].unique())

    def write_all(self):
        """Write all outputs."""
        self._write_raw_csvs()
        self._write_summary_excel()
        self._plot_ce_planes()
        self._plot_ceac()
        self._plot_icer_histograms()
        self._plot_convergence_icer()
        self._embed_plots_in_workbook()
        print(f"  PSA × HTA outputs written to: {self.output_dir}/")

    # ── Raw CSVs ─────────────────────────────────────────────────────────────

    def _write_raw_csvs(self):
        self.incremental_df.to_csv(
            self.output_dir / "psa_hta_incremental.csv", index=False
        )
        self.arm_outcomes_df.to_csv(
            self.output_dir / "psa_hta_arm_outcomes.csv", index=False
        )

    # ── Summary Excel ─────────────────────────────────────────────────────────

    def _write_summary_excel(self):
        """
        6-sheet Excel workbook:
          1. epi_parameter_summary
          2. hta_parameter_summary
          3. arm_outcome_summary
          4. incremental_summary
          5. ceac_data
          6. raw_incremental
        """
        path = self.output_dir / "psa_hta_summary.xlsx"
        with pd.ExcelWriter(path, engine="openpyxl") as writer:

            # 1. Epi parameter summary
            self.epi_sampler.get_parameter_summary().to_excel(
                writer, sheet_name="epi_parameter_summary", index=False
            )

            # 2. HTA parameter summary
            self.hta_sampler.get_parameter_summary().to_excel(
                writer, sheet_name="hta_parameter_summary", index=False
            )

            # 3. Arm outcome summary
            arm_summary = self._compute_arm_outcome_summary()
            arm_summary.to_excel(
                writer, sheet_name="arm_outcome_summary", index=False
            )

            # 4. Incremental summary
            inc_summary = self._compute_incremental_summary()
            inc_summary.to_excel(
                writer, sheet_name="incremental_summary", index=False
            )

            # 5. CEAC data
            ceac_df = self._compute_ceac(self.incremental_df, self.WTP_RANGE)
            ceac_pivot = ceac_df.pivot(
                index="wtp", columns="comparison", values="prob_ce"
            ).reset_index()
            ceac_pivot.to_excel(writer, sheet_name="ceac_data", index=False)

            # 6. Raw incremental
            self.incremental_df.to_excel(
                writer, sheet_name="raw_incremental", index=False
            )

            # 7. Failed draws (if any)
            if self.failed_draws:
                pd.DataFrame(self.failed_draws)[["draw_index", "error"]].to_excel(
                    writer, sheet_name="failed_draws", index=False
                )

    def _embed_plots_in_workbook(self):
        """
        Add generated PSA figures to the summary workbook.

        The PNG files remain available as standalone artifacts, but reviewer-facing
        output should not require manually opening separate image files.  Embedding
        preserves the existing Matplotlib CE plane/CEAC styling without duplicating
        that logic as native Excel chart code.
        """
        path = self.output_dir / "psa_hta_summary.xlsx"
        if not path.exists():
            return

        figure_paths = []
        ceac_path = self.output_dir / "ceac.png"
        if ceac_path.exists():
            figure_paths.append(("Cost-Effectiveness Acceptability Curve", ceac_path))

        for comp in self.comparisons:
            safe_name = comp.replace(" ", "_").replace("/", "_")
            ce_plane = self.output_dir / f"ce_plane_{safe_name}.png"
            if ce_plane.exists():
                figure_paths.append((f"Cost-Effectiveness Plane — {comp}", ce_plane))

        for comp in self.comparisons:
            safe_name = comp.replace(" ", "_").replace("/", "_")
            hist = self.output_dir / f"icer_histogram_{safe_name}.png"
            if hist.exists():
                figure_paths.append((f"ICER Distribution — {comp}", hist))

        for comp in self.comparisons:
            safe_name = comp.replace(" ", "_").replace("/", "_")
            conv = self.output_dir / f"convergence_icer_{safe_name}.png"
            if conv.exists():
                figure_paths.append((f"ICER Convergence — {comp}", conv))

        if not figure_paths:
            return

        from openpyxl import load_workbook
        from openpyxl.drawing.image import Image
        from openpyxl.styles import Font, PatternFill, Alignment

        wb = load_workbook(path)
        if "PSA Figures" in wb.sheetnames:
            del wb["PSA Figures"]
        ws = wb.create_sheet("PSA Figures", 0)
        ws.sheet_view.showGridLines = False
        ws.column_dimensions["A"].width = 4
        for col in ["B", "C", "D", "E", "F", "G", "H", "I", "J"]:
            ws.column_dimensions[col].width = 16

        title = ws.cell(row=1, column=2)
        title.value = "PSA × HTA Figures"
        title.font = Font(name="Arial", bold=True, size=13, color="FFFFFF")
        title.fill = PatternFill("solid", fgColor="1F3864")
        title.alignment = Alignment(horizontal="left", vertical="center")
        ws.merge_cells(start_row=1, start_column=2, end_row=1, end_column=9)
        ws.row_dimensions[1].height = 24

        note = ws.cell(row=2, column=2)
        note.value = (
            "Embedded copies of generated PSA figures. Standalone PNG files are "
            "also written to the output folder."
        )
        note.font = Font(name="Arial", italic=True, size=9, color="808080")
        note.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.merge_cells(start_row=2, start_column=2, end_row=2, end_column=9)
        ws.row_dimensions[2].height = 24

        row = 4
        for figure_title, image_path in figure_paths:
            header = ws.cell(row=row, column=2)
            header.value = figure_title
            header.font = Font(name="Arial", bold=True, size=11, color="FFFFFF")
            header.fill = PatternFill("solid", fgColor="2E5E8E")
            header.alignment = Alignment(horizontal="left", vertical="center")
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=9)
            ws.row_dimensions[row].height = 20
            row += 1

            img = Image(str(image_path))
            img.width = 720
            img.height = 450
            ws.add_image(img, f"B{row}")
            row += 25

        wb.save(path)

    def _compute_arm_outcome_summary(self) -> pd.DataFrame:
        rows = []
        for arm in self.arm_outcomes_df["arm"].unique():
            sub = self.arm_outcomes_df[self.arm_outcomes_df["arm"] == arm]
            for col in ["total_qalys_disc", "total_costs_disc",
                        "total_costs_disc_societal", "cumul_new_infections"]:
                if col not in sub.columns:
                    continue
                vals = sub[col].dropna()
                rows.append({
                    "arm": arm,
                    "metric": col,
                    "mean": vals.mean(),
                    "median": vals.median(),
                    "p2.5": vals.quantile(0.025),
                    "p97.5": vals.quantile(0.975),
                    "min": vals.min(),
                    "max": vals.max(),
                    "n": len(vals),
                })
        return pd.DataFrame(rows)

    def _compute_incremental_summary(self) -> pd.DataFrame:
        rows = []
        for comp in self.comparisons:
            sub = self.incremental_df[self.incremental_df["comparison"] == comp]
            n = len(sub)
            for metric in ["delta_cost", "delta_qaly", "icer", "nmb",
                           "infections_averted"]:
                if metric not in sub.columns:
                    continue
                vals = sub[metric].replace([np.inf, -np.inf], np.nan).dropna()
                if len(vals) == 0:
                    continue
                rows.append({
                    "comparison": comp,
                    "metric": metric,
                    "mean": vals.mean(),
                    "median": vals.median(),
                    "p2.5": vals.quantile(0.025),
                    "p97.5": vals.quantile(0.975),
                    "n_valid": len(vals),
                })

            # P(cost-effective) at configured WTP
            if "nmb" in sub.columns:
                nmb_vals = sub["nmb"].replace([np.inf, -np.inf], np.nan).dropna()
                p_ce = float((nmb_vals > 0).mean()) if len(nmb_vals) > 0 else np.nan
                rows.append({
                    "comparison": comp,
                    "metric": f"P(CE) at WTP={self.wtp:,.0f}",
                    "mean": p_ce,
                    "median": np.nan,
                    "p2.5": np.nan,
                    "p97.5": np.nan,
                    "n_valid": len(nmb_vals),
                })
        return pd.DataFrame(rows)

    # ── CEAC computation ──────────────────────────────────────────────────────

    def _compute_ceac(self, incremental_df, wtp_range) -> pd.DataFrame:
        """
        For each WTP threshold λ and each comparison:
          NMB = delta_qaly * λ - delta_cost
          P(CE) = proportion of draws where NMB > 0
        """
        rows = []
        for wtp in wtp_range:
            for comp in self.comparisons:
                sub = incremental_df[incremental_df["comparison"] == comp]
                dc = sub["delta_cost"].replace([np.inf, -np.inf], np.nan).dropna()
                dq = sub["delta_qaly"].replace([np.inf, -np.inf], np.nan).dropna()
                # Align indices
                valid_idx = dc.index.intersection(dq.index)
                if len(valid_idx) == 0:
                    rows.append({"wtp": wtp, "comparison": comp, "prob_ce": np.nan})
                    continue
                nmb = dq.loc[valid_idx] * wtp - dc.loc[valid_idx]
                p_ce = float((nmb > 0).mean())
                rows.append({"wtp": wtp, "comparison": comp, "prob_ce": p_ce})
        return pd.DataFrame(rows)

    # ── Plots ─────────────────────────────────────────────────────────────────

    def _plot_ce_planes(self):
        """One CE plane scatter plot per comparison."""
        for comp in self.comparisons:
            sub = self.incremental_df[self.incremental_df["comparison"] == comp]
            dq = sub["delta_qaly"].replace([np.inf, -np.inf], np.nan).dropna()
            dc = sub["delta_cost"].replace([np.inf, -np.inf], np.nan).dropna()
            valid_idx = dq.index.intersection(dc.index)
            if len(valid_idx) == 0:
                continue

            dq_vals = dq.loc[valid_idx].values
            dc_vals = dc.loc[valid_idx].values
            mean_dq = float(np.mean(dq_vals))
            mean_dc = float(np.mean(dc_vals))

            fig, ax = plt.subplots(figsize=(8, 6))

            # Scatter of draws
            ax.scatter(dq_vals, dc_vals, alpha=0.3, s=10, color="#4472C4",
                       label=f"PSA draws (n={len(valid_idx)})")

            # Mean point
            ax.scatter([mean_dq], [mean_dc], color="red", s=80, zorder=5,
                       marker="*", label=f"Mean (ΔE={mean_dq:.3f}, ΔC={mean_dc:,.0f})")

            # WTP threshold line through origin
            x_range = np.linspace(min(dq_vals.min(), 0) * 1.1,
                                  max(dq_vals.max(), 0) * 1.1, 100)
            ax.plot(x_range, self.wtp * x_range, "k--", linewidth=1,
                    label=f"WTP = €{self.wtp:,.0f}/QALY")

            # Reference lines
            ax.axhline(0, color="gray", linewidth=0.5)
            ax.axvline(0, color="gray", linewidth=0.5)

            # Quadrant labels
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            mid_x = (xlim[0] + xlim[1]) / 2
            mid_y = (ylim[0] + ylim[1]) / 2
            ax.text(xlim[1] * 0.9, ylim[1] * 0.9, "NE\n(more costly,\nmore effective)",
                    ha="right", va="top", fontsize=7, color="gray")
            ax.text(xlim[1] * 0.9, ylim[0] * 0.9, "SE\n(dominant)",
                    ha="right", va="bottom", fontsize=7, color="green")
            ax.text(xlim[0] * 0.9 if xlim[0] < 0 else -abs(xlim[1]) * 0.1,
                    ylim[1] * 0.9, "NW\n(dominated)",
                    ha="left", va="top", fontsize=7, color="red")

            ax.set_xlabel("ΔQALYs (incremental effectiveness)")
            ax.set_ylabel("ΔCosts (incremental costs, €)")
            ax.set_title(f"CE Plane: {comp}\n(N={len(valid_idx)} draws)")
            ax.legend(fontsize=8)
            plt.tight_layout()
            safe_name = comp.replace(" ", "_").replace("/", "_")
            fig.savefig(self.output_dir / f"ce_plane_{safe_name}.png", dpi=150)
            plt.close(fig)

    def _plot_ceac(self):
        """CEAC: P(CE) vs WTP for all comparisons."""
        ceac_df = self._compute_ceac(self.incremental_df, self.WTP_RANGE)
        if ceac_df.empty:
            return

        colors = ["#4472C4", "#ED7D31", "#A9D18E", "#FF0000", "#7030A0"]
        fig, ax = plt.subplots(figsize=(10, 6))

        for j, comp in enumerate(self.comparisons):
            sub = ceac_df[ceac_df["comparison"] == comp].sort_values("wtp")
            color = colors[j % len(colors)]
            ax.plot(sub["wtp"] / 1000, sub["prob_ce"], linewidth=2,
                    label=comp, color=color)

        ax.axvline(self.wtp / 1000, color="black", linestyle="--", linewidth=1,
                   label=f"WTP = €{self.wtp/1000:.0f}k")
        ax.set_xlabel("Willingness-to-Pay (€ thousands per QALY)")
        ax.set_ylabel("P(cost-effective)")
        ax.set_ylim(0, 1)
        ax.set_title("Cost-Effectiveness Acceptability Curve (CEAC)")
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        fig.savefig(self.output_dir / "ceac.png", dpi=150)
        plt.close(fig)

    def _plot_icer_histograms(self):
        """ICER histogram per comparison (excluding dominated/dominant draws)."""
        for comp in self.comparisons:
            sub = self.incremental_df[self.incremental_df["comparison"] == comp]
            icer_vals = sub["icer"].replace([np.inf, -np.inf], np.nan).dropna()
            if len(icer_vals) == 0:
                continue

            # Remove extreme outliers for display (>99th percentile)
            p99 = icer_vals.quantile(0.99)
            p1 = icer_vals.quantile(0.01)
            filtered = icer_vals[(icer_vals >= p1) & (icer_vals <= p99)]
            n_excluded = len(icer_vals) - len(filtered)

            fig, ax = plt.subplots(figsize=(8, 5))
            ax.hist(filtered, bins=min(50, max(10, len(filtered) // 5)),
                    color="#4472C4", alpha=0.7, edgecolor="white")
            ax.axvline(self.wtp, color="red", linestyle="--", linewidth=1.5,
                       label=f"WTP = €{self.wtp:,.0f}")
            ax.axvline(filtered.mean(), color="orange", linestyle=":",
                       linewidth=1.5, label=f"Mean ICER = €{filtered.mean():,.0f}")
            ax.set_xlabel("ICER (€/QALY)")
            ax.set_ylabel("Frequency")
            title = f"ICER Distribution: {comp}"
            if n_excluded > 0:
                title += f"\n({n_excluded} extreme draws excluded from display)"
            ax.set_title(title)
            ax.legend(fontsize=9)
            plt.tight_layout()
            safe_name = comp.replace(" ", "_").replace("/", "_")
            fig.savefig(self.output_dir / f"icer_histogram_{safe_name}.png", dpi=150)
            plt.close(fig)

    def _plot_convergence_icer(self):
        """Running mean ICER ± 95% CI per comparison."""
        for comp in self.comparisons:
            sub = self.incremental_df[self.incremental_df["comparison"] == comp]
            icer_vals = sub["icer"].replace([np.inf, -np.inf], np.nan).dropna()
            if len(icer_vals) < 2:
                continue

            vals = icer_vals.values
            n_total = len(vals)
            checkpoints = sorted(set(
                [min(5, n_total)] +
                list(range(max(5, n_total // 10), n_total, max(1, n_total // 10))) +
                [n_total]
            ))

            ns, means, lo_ci, hi_ci = [], [], [], []
            for cp in checkpoints:
                subset = vals[:cp]
                mean = np.mean(subset)
                std = np.std(subset, ddof=1) if cp > 1 else 0
                ci_half = 1.96 * std / np.sqrt(cp) if cp > 1 else 0
                ns.append(cp)
                means.append(mean)
                lo_ci.append(mean - ci_half)
                hi_ci.append(mean + ci_half)

            fig, ax = plt.subplots(figsize=(8, 5))
            ax.plot(ns, means, color="#4472C4", linewidth=2, label="Running mean ICER")
            ax.fill_between(ns, lo_ci, hi_ci, alpha=0.2, color="#4472C4",
                            label="95% CI")
            ax.axhline(self.wtp, color="red", linestyle="--", linewidth=1,
                       label=f"WTP = €{self.wtp:,.0f}")
            ax.set_xlabel("Number of PSA draws")
            ax.set_ylabel("ICER (€/QALY)")
            ax.set_title(f"ICER Convergence: {comp}")
            ax.legend(fontsize=9)
            ax.grid(True, alpha=0.3)
            plt.tight_layout()
            safe_name = comp.replace(" ", "_").replace("/", "_")
            fig.savefig(
                self.output_dir / f"convergence_icer_{safe_name}.png", dpi=150
            )
            plt.close(fig)
