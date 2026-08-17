"""
psa_output_writer.py — PSA result serialisation.

Writes:
  - Raw CSV: parameter draws matrix (N x K), outcome matrix (N x M)
  - Summary Excel workbook (6 sheets)
  - Matplotlib plots: tornado, histogram, convergence

All output paths are under psa_config["outputs"]["directory"]["value"].

V11.10.4 — 2026-04-27
"""

import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')   # non-interactive backend — no display required
import matplotlib.pyplot as plt
from pathlib import Path
from scipy import stats
from scipy.stats import rankdata


class PSAOutputWriter:
    """
    Writes PSA outputs: raw CSVs, summary Excel workbook, and diagnostic plots.

    Parameters
    ----------
    draws_df      : pd.DataFrame  shape (N, K), columns = dot-paths
    outcomes_df   : pd.DataFrame  shape (N, M), columns = metric names
    sampler       : PSASampler    provides get_parameter_summary()
    psa_config    : dict          full PSA config dict
    output_dir    : Path          output directory (already created)
    """

    def __init__(self, draws_df, outcomes_df, sampler, psa_config, output_dir,
                 failed_draws=None):
        self.draws_df = draws_df
        self.outcomes_df = outcomes_df
        self.sampler = sampler
        self.psa_config = psa_config
        self.output_dir = Path(output_dir)
        self.failed_draws = failed_draws or []

    def write_all(self):
        """Write all enabled outputs."""
        cfg = self.psa_config["outputs"]

        if cfg["raw_draws"].get("enabled", True):
            self._write_raw_csv()

        if cfg["summary_excel"].get("enabled", True):
            self._write_summary_excel()

        plots_cfg = cfg["plots"]
        fmt = plots_cfg["format"]["value"]
        dpi = plots_cfg["dpi"]["value"]

        if plots_cfg["tornado"].get("enabled", True):
            self._plot_tornado(fmt, dpi)

        if plots_cfg["histogram"].get("enabled", True):
            self._plot_histograms(fmt, dpi)

        if plots_cfg["convergence"].get("enabled", True):
            self._plot_convergence(fmt, dpi)

        print(f"  Outputs written to: {self.output_dir}/")

    # ── Raw CSV ─────────────────────────────────────────────────────────────

    def _write_raw_csv(self):
        """Write parameter draws and outcome matrices as CSV."""
        self.draws_df.to_csv(self.output_dir / "psa_draws.csv", index=True)
        self.outcomes_df.to_csv(self.output_dir / "psa_outcomes.csv", index=True)

    # ── Summary Excel ───────────────────────────────────────────────────────

    def _write_summary_excel(self):
        """
        Write Excel workbook with 6 sheets:
          1. parameter_summary  — from sampler.get_parameter_summary()
          2. outcome_summary    — per-metric: mean, median, SD, 95% CI, min, max
          3. raw_draws          — full N x K matrix
          4. raw_outcomes       — full N x M matrix
          5. tornado_data       — Spearman rank correlation of each param vs tornado metric
          6. convergence        — running mean and CI at checkpoints
        """
        excel_path = self.output_dir / "psa_summary.xlsx"

        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            # 1. Parameter summary
            param_summary = self.sampler.get_parameter_summary()
            param_summary.to_excel(writer, sheet_name="parameter_summary", index=False)

            # 2. Outcome summary
            outcome_summary = self._compute_outcome_summary()
            outcome_summary.to_excel(writer, sheet_name="outcome_summary", index=False)

            # 3. Raw draws
            self.draws_df.to_excel(writer, sheet_name="raw_draws")

            # 4. Raw outcomes
            self.outcomes_df.to_excel(writer, sheet_name="raw_outcomes")

            # 5. Tornado data
            tornado_metric = self.psa_config["outputs"]["plots"]["tornado"].get(
                "metric", "cumulative_infections"
            )
            if tornado_metric in self.outcomes_df.columns:
                tornado_data = self._compute_rank_correlations(tornado_metric)
                tornado_data.to_excel(writer, sheet_name="tornado_data", index=False)

            # 6. Convergence data
            conv_metric = self.psa_config["outputs"]["plots"]["convergence"].get(
                "metric", "cumulative_infections"
            )
            if conv_metric in self.outcomes_df.columns:
                conv_data = self._compute_convergence_data(conv_metric)
                conv_data.to_excel(writer, sheet_name="convergence", index=False)

            # 7. Failed draws (if any)
            if self.failed_draws:
                failed_df = pd.DataFrame(self.failed_draws)[["draw_index", "error"]]
                failed_df.to_excel(writer, sheet_name="failed_draws", index=False)

    def _compute_outcome_summary(self) -> pd.DataFrame:
        """Compute per-metric summary statistics."""
        rows = []
        for col in self.outcomes_df.columns:
            vals = self.outcomes_df[col].dropna()
            if len(vals) == 0:
                continue
            n = len(vals)
            mean = vals.mean()
            std = vals.std()
            ci_lo = mean - 1.96 * std / np.sqrt(n) if n > 1 else np.nan
            ci_hi = mean + 1.96 * std / np.sqrt(n) if n > 1 else np.nan

            rows.append({
                "metric": col,
                "n_draws": n,
                "mean": mean,
                "median": vals.median(),
                "std": std,
                "ci_95_lo": ci_lo,
                "ci_95_hi": ci_hi,
                "min": vals.min(),
                "max": vals.max(),
                "p2.5": vals.quantile(0.025),
                "p97.5": vals.quantile(0.975),
            })
        return pd.DataFrame(rows)

    # ── Rank correlation (tornado data) ─────────────────────────────────────

    def _compute_rank_correlations(self, metric_col: str) -> pd.DataFrame:
        """
        Compute Spearman rank correlations between each parameter
        column in draws_df and outcomes_df[metric_col].

        Returns DataFrame with columns: parameter, spearman_rho, abs_rho,
        sorted by abs_rho descending.

        Note: This is a Spearman correlation approximation, not a full PRCC
        (which would require partial regression on ranks). Labelled accordingly.
        """
        outcome = self.outcomes_df[metric_col].values
        rows = []

        for param_col in self.draws_df.columns:
            param_vals = self.draws_df[param_col].values

            # Skip if all values are identical (no variation)
            if np.std(param_vals) == 0 or np.std(outcome) == 0:
                rows.append({
                    "parameter": param_col,
                    "spearman_rho": 0.0,
                    "abs_rho": 0.0,
                })
                continue

            rho, pval = stats.spearmanr(param_vals, outcome)
            rows.append({
                "parameter": param_col,
                "spearman_rho": rho if not np.isnan(rho) else 0.0,
                "abs_rho": abs(rho) if not np.isnan(rho) else 0.0,
            })

        df = pd.DataFrame(rows).sort_values("abs_rho", ascending=False)
        return df.reset_index(drop=True)

    # ── Convergence data ────────────────────────────────────────────────────

    def _compute_convergence_data(self, metric_col: str) -> pd.DataFrame:
        """Compute running mean and 95% CI at regular checkpoints."""
        vals = self.outcomes_df[metric_col].values
        n_total = len(vals)

        if n_total == 0:
            return pd.DataFrame(columns=["n_draws", "running_mean", "ci_lo", "ci_hi"])
        if n_total == 1:
            return pd.DataFrame({"n_draws": [1], "running_mean": [float(vals[0])],
                                 "ci_lo": [np.nan], "ci_hi": [np.nan]})

        # Checkpoints: every 10% of total draws, plus first 10 and final
        checkpoints = sorted(set(
            [min(10, n_total)] +
            list(range(max(10, n_total // 10), n_total, max(1, n_total // 10))) +
            [n_total]
        ))

        rows = []
        for cp in checkpoints:
            subset = vals[:cp]
            mean = np.mean(subset)
            std = np.std(subset, ddof=1) if cp > 1 else 0
            ci_half = 1.96 * std / np.sqrt(cp) if cp > 1 else 0
            rows.append({
                "n_draws": cp,
                "running_mean": mean,
                "ci_lo": mean - ci_half,
                "ci_hi": mean + ci_half,
            })

        return pd.DataFrame(rows)

    # ── Plots ───────────────────────────────────────────────────────────────

    def _plot_tornado(self, fmt, dpi):
        """
        Horizontal bar chart of Spearman rank correlation values.
        Positive rho = red/warm. Negative rho = blue/cool.
        """
        tornado_cfg = self.psa_config["outputs"]["plots"]["tornado"]
        metric = tornado_cfg.get("metric", "cumulative_infections")
        top_n = tornado_cfg.get("top_n", 9)

        if metric not in self.outcomes_df.columns:
            warnings.warn(f"Tornado: metric '{metric}' not in outcomes. Skipping.")
            return

        corr_df = self._compute_rank_correlations(metric)
        corr_df = corr_df.head(min(top_n, len(corr_df)))

        if len(corr_df) == 0:
            return

        # Shorten parameter names for readability
        labels = [_short_param_name(p) for p in corr_df["parameter"]]
        values = corr_df["spearman_rho"].values
        colors = ["#d62728" if v >= 0 else "#1f77b4" for v in values]

        fig, ax = plt.subplots(figsize=(10, max(4, len(labels) * 0.5)))
        y_pos = range(len(labels))
        ax.barh(y_pos, values, color=colors, height=0.6)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=9)
        ax.set_xlabel("Spearman Rank Correlation")
        ax.set_title(f"Tornado Diagram: {metric}\n(N = {len(self.outcomes_df)})")
        ax.axvline(0, color="black", linewidth=0.5)
        ax.invert_yaxis()
        plt.tight_layout()
        fig.savefig(self.output_dir / f"tornado_{metric}.{fmt}", dpi=dpi)
        plt.close(fig)

    def _plot_histograms(self, fmt, dpi):
        """One histogram per metric in psa_config plots.histogram.metrics."""
        hist_cfg = self.psa_config["outputs"]["plots"]["histogram"]
        metrics = hist_cfg.get("metrics", [])

        for metric in metrics:
            if metric not in self.outcomes_df.columns:
                warnings.warn(f"Histogram: metric '{metric}' not in outcomes. Skipping.")
                continue

            vals = self.outcomes_df[metric].dropna()
            if len(vals) == 0:
                continue

            fig, ax = plt.subplots(figsize=(8, 5))
            ax.hist(vals, bins=min(50, max(10, len(vals) // 5)),
                    color="#2196F3", alpha=0.7, edgecolor="white")
            ax.axvline(vals.mean(), color="red", linestyle="--", linewidth=1.5,
                       label=f"Mean: {vals.mean():.4g}")
            ax.axvline(vals.median(), color="orange", linestyle=":",
                       linewidth=1.5, label=f"Median: {vals.median():.4g}")
            ax.set_xlabel(metric)
            ax.set_ylabel("Frequency")
            ax.set_title(f"PSA Distribution: {metric}\n(N = {len(vals)})")
            ax.legend(fontsize=9)
            plt.tight_layout()
            fig.savefig(self.output_dir / f"histogram_{metric}.{fmt}", dpi=dpi)
            plt.close(fig)

    def _plot_convergence(self, fmt, dpi):
        """
        X-axis: draw count [1..N].
        Y-axis: running mean of target metric.
        Shaded band: running 95% CI (mean ± 1.96 * running_std / sqrt(n)).
        """
        conv_cfg = self.psa_config["outputs"]["plots"]["convergence"]
        metric = conv_cfg.get("metric", "cumulative_infections")

        if metric not in self.outcomes_df.columns:
            warnings.warn(f"Convergence: metric '{metric}' not in outcomes. Skipping.")
            return

        vals = self.outcomes_df[metric].values
        n_total = len(vals)
        if n_total < 2:
            return

        # Compute running statistics at every draw
        ns = np.arange(1, n_total + 1)
        running_mean = np.cumsum(vals) / ns
        running_var = np.zeros(n_total)
        for i in range(1, n_total):
            running_var[i] = np.var(vals[:i + 1], ddof=1)
        running_std = np.sqrt(running_var)
        ci_half = 1.96 * running_std / np.sqrt(ns)
        ci_half[0] = 0  # undefined for n=1

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(ns, running_mean, color="#1f77b4", linewidth=1.5, label="Running mean")
        ax.fill_between(ns, running_mean - ci_half, running_mean + ci_half,
                        alpha=0.2, color="#1f77b4", label="95% CI")
        ax.set_xlabel("Number of PSA Draws")
        ax.set_ylabel(f"Running Mean: {metric}")
        ax.set_title(f"PSA Convergence: {metric}\n(Total N = {n_total})")
        ax.legend(fontsize=9)
        plt.tight_layout()
        fig.savefig(self.output_dir / f"convergence_{metric}.{fmt}", dpi=dpi)
        plt.close(fig)


# ── Utility ─────────────────────────────────────────────────────────────────

def _short_param_name(dot_path: str) -> str:
    """Shorten a dot-path for plot labels.
    e.g. 'transmission_model.disease.transmission.beta_acute' -> 'beta_acute'
    """
    parts = dot_path.split(".")
    # Take last 1-2 segments depending on uniqueness
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return parts[-1]
