"""
psa_metrics.py — Outcome metric extraction from simulation DataFrames.

Configured entirely from the psa_config["metrics"]["extract"] list.
No hardcoded metric calculations — all metric logic is driven by config.

V11.10.1 — 2026-04-27
"""

import warnings
import numpy as np
import pandas as pd


class MetricExtractor:
    """
    Extracts scalar outcome metrics from a simulation output DataFrame.

    Parameters
    ----------
    metric_configs : list[dict]
        The list of metric definition dicts from psa_config["metrics"]["extract"].
        Each dict must have: "name", "column", "aggregation".
        Optional: "year_range" ([start, end] inclusive), "year" (for value_at_year).
    """

    SUPPORTED_AGGREGATIONS = frozenset(
        {"sum", "mean", "value_at_year", "final", "max", "min"}
    )

    def __init__(self, metric_configs: list):
        self._configs = []
        for mc in metric_configs:
            name = mc["name"]
            agg = mc["aggregation"]
            if agg not in self.SUPPORTED_AGGREGATIONS:
                raise ValueError(
                    f"Metric '{name}': unsupported aggregation '{agg}'. "
                    f"Supported: {sorted(self.SUPPORTED_AGGREGATIONS)}"
                )
            self._configs.append(mc)

    def extract(self, df: pd.DataFrame) -> dict:
        """
        Extract all configured metrics from a simulation DataFrame.

        Returns
        -------
        dict
            {metric_name: float}  for each configured metric.
        Raises KeyError if a required column is missing from df.
        """
        results = {}
        for mc in self._configs:
            name = mc["name"]
            col = mc["column"]
            agg = mc["aggregation"]

            # Check column exists
            if col not in df.columns:
                raise KeyError(
                    f"Metric '{name}' references column '{col}' which is not "
                    f"in the simulation output DataFrame. "
                    f"Available columns: {list(df.columns)}"
                )

            # Apply year range mask if specified
            year_range = mc.get("year_range")
            if year_range is not None:
                y0, y1 = year_range
                mask = (df["year"] >= y0) & (df["year"] <= y1)
                subset = df.loc[mask]
                if len(subset) == 0:
                    warnings.warn(
                        f"Metric '{name}': year_range [{y0}, {y1}] has no "
                        f"matching rows in the simulation output "
                        f"(years: {df['year'].min()}-{df['year'].max()}). "
                        f"Returning 0.0.",
                        UserWarning,
                    )
                    results[name] = 0.0
                    continue
            else:
                subset = df

            # Compute aggregation
            if agg == "sum":
                results[name] = float(subset[col].sum())
            elif agg == "mean":
                results[name] = float(subset[col].mean())
            elif agg == "max":
                results[name] = float(subset[col].max())
            elif agg == "min":
                results[name] = float(subset[col].min())
            elif agg == "final":
                results[name] = float(df[col].iloc[-1])
            elif agg == "value_at_year":
                target_year = mc["year"]
                idx = (df["year"] - target_year).abs().idxmin()
                results[name] = float(df.loc[idx, col])

        return results

    def metric_names(self) -> list:
        """Ordered list of metric names as they will appear in extract() output."""
        return [mc["name"] for mc in self._configs]
