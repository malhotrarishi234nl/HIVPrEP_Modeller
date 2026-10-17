# Local model workbench

The workbench is a local browser interface for the model's supported commands.
It does not require an editor, and every run keeps its resolved settings, logs,
status, and scientific output in a new directory under `outputs/workbench/`.

## First setup

Python 3.11 is required.

- macOS: double-click `Setup Workbench.command` once, then double-click
  `Launch Workbench.command` whenever you want to use it.
- Windows: double-click `Setup Workbench.bat` once, then double-click
  `Launch Workbench.bat` whenever you want to use it.

The setup creates `.venv-workbench` inside the repository. It does not modify a
system Python installation. The launch file starts the interface and opens it
in the default browser. It binds only to `127.0.0.1`, so it is not exposed to
other machines on the network. Close the terminal window or press Ctrl+C there
to stop the interface.

If double-click launch is blocked by an operating-system security setting, open
a terminal in the repository and run the same file from there.

## Navigation

The sidebar is grouped by task. **Workflow**: Home, Build and run, Results,
Compare results. **Evidence**: Evidence register, Data and targets, Model guide.
**System**: Technical details, Run history, Setup. A "Working analysis" card at the
bottom of the sidebar always shows what is currently configured and whether it is
ready to run.

## Build and run

Choose a mode at the top of the page:

- **Historical fit** re-simulates 1980-2025 so the result can be validated
  against observed data.
- **Forecast and economics** projects the policy matrix from 2025 and computes
  costs, QALYs, adverse events, ICER and net monetary benefit.

Both modes start from the recorded configuration or a saved one ("Start from" and
Load; saved configurations are also listed on Home) and then follow numbered tabs:

1. **Inputs.** Each parameter table shows the recorded value, an editable
   "This analysis" column, the literature 95% interval, the allowed range, the unit
   and the source. Forecast mode adds policy comparisons, cost scope, and cost,
   utility and adverse-event inputs.
2. **Run design.** Diagnosis pathway (historical), horizon (forecast), stochastic
   runs, seeds, workers and the analysis name.
3. **Review and run.** Summary tiles, the list of changed inputs, validation
   messages, and Check, Estimate (historical only), Save and Run actions.

Saved configurations live in `operator_configs/workbench_recipes/` (historical
`*.json`, forecast `economic-*.json`) and never overwrite one another.


Parameter changes use the same bounded contract as the command-line runner. The
workbench records explicit values in the resolved plan, sends them through
`run_model.py --set`, updates coupled values together, and preserves the effective
configuration beside completed outputs. The test ranges are configuration
guardrails, not confidence intervals or scientific approval.

The simulation start year is not a free scalar control. The year, population
structure, epidemic seed, and provenance form one initialization bundle; changing
the displayed output period does not infer a new starting population.

## Results

Pick any built-in or completed local result. Historical results have tabs for
Calibration fit, Validation against data (calibration targets and held-out series
shown separately), Epidemic trends, Runs and files, and Export. Forecast results
have Headline (incremental cost, QALYs, infections averted, NMB, ICER), Cost
components, Adverse events, Epidemiology, Budget impact, and Exact tables with the
economic workbook download. Manual import of an output folder remains available
under "Open a result from another folder".

## Evidence register

Built directly from the configuration files, so it cannot drift from what runs.
Lists every parameter with value, unit, declared distribution, 95% interval
(from the distribution, else recorded bounds), evidence status, source and config
path. Tabs: Parameter register (filter, search, show gaps, CSV download),
Distributions (density with base-case value and 95% band), and Cost matrix.
"Not recorded" marks a genuine gap in the configuration.

File paths, raw status codes, hashes, and other audit fields remain available in
the technical provenance section instead of leading the results page.

Each report receives a compact result reference derived from the
relative source filenames and their SHA-256 hashes. Figure captions point to
that reference and `source_inventory.csv`; `provenance.json` stores the same
identifier. This is a result identity for traceability, not a publication DOI.

CSV, JSON, SVG, and offline HTML files remain in the report directory as audit
companions. They are not the primary collaborator deliverable.

Annual conversion is explicit: event flows are summed, stocks use the year-end
value, start-of-period diagnostics use the first value, and declared rates use
their documented rule. Unknown columns stay in the raw preview rather than
being aggregated by guesswork.

The initial result is the confirmed 2008-break candidate 1787 from the recorded
2026-09-29 conference lineage. The primary chart shows the ensemble median and
central 90% stochastic range, with matching observed calibration points where
available. Exact observed-versus-simulated target values and implausibility
scores are shown in a separate fit table; individual seed trajectories are
optional. The fit table uses both text and restrained color to identify whether
the primary implausibility score is within the defined threshold of 3; color is
never the only signal. No generic percentage cutoff is invented. This is
retained historical calibration evidence, not held-out validation, and its
selected configuration is not offered as a current-code rerun.

### Comparing results

Choose two results from the same catalog. The default comparison uses ensemble
medians and keeps exact tables behind an expander, rather than asking the user
to type filesystem paths or presenting every seed as a separate colored line.

### Data and targets

The page reads `data/external_inputs.json`, checks whether each input exists,
and compares its current SHA-256 with the recorded identity. It also displays
the tracked target registers, available sheets in `DiseaseData.xlsx`, and a
bounded raw preview of the selected worksheet.

An added observation can be proposed as a downloadable JSON patch bound to the
source workbook hash and sheet. The patch is not applied automatically; it
must be reviewed before producing a new workbook and target register. This
prevents an interface edit from silently changing an established target set.

## Command-line alternative

The browser and command line use the same recipe service. From the repository
root on macOS or Linux:

```bash
.venv-workbench/bin/python -m model_workbench list
.venv-workbench/bin/python -m model_workbench check basic-stochastic-m0
.venv-workbench/bin/python -m model_workbench benchmark basic-stochastic-m1 --set p=0.2
.venv-workbench/bin/python -m model_workbench run basic-stochastic-m0 --set replicates=3
.venv-workbench/bin/python -m model_workbench check basic-stochastic-m0 \
  --set parameter:transmission_model.disease.transmission.advanced.beta_per_route.anal_receptive.chronic.value=0.016
```

Windows uses `.venv-workbench\Scripts\python.exe` in place of
`.venv-workbench/bin/python`.

Recipes contain typed inputs and controls, not shell commands. The service
rebuilds and compares a plan immediately before execution, invokes subprocesses
without a shell, refuses non-empty output directories, and verifies expected
files after every completed trajectory.

## Data distribution

The tracked input manifest currently points to the sibling `../data/raw/`
layout. Some source files and the complete historical result vault are too
large for an ordinary source repository, and their redistribution status is
still marked for review. Before collaborator handoff, either:

1. place the approved input pack in the recorded sibling layout; or
2. move large approved files to Git LFS or a separate versioned data release,
   then update `data/external_inputs.json` with the final relative paths and
   unchanged authoritative hashes.

Do not commit only a path change: Setup check must report both availability and
a matching hash on the collaborator's machine.

## Current functional boundary

The workbench can inspect preserved historical calibration evidence, overlay
recorded calibration and comparison data, run bounded stochastic M0/M1
parameter sensitivities, and run the repository's paired policy and economic
scenario matrix. Historical and economic batches accept a portable limit of
one to eight workers, or all detected processors; Setup includes a model-free
child-process check.

Recalibration, PSA, new future-projection variants, and coordinated alternative
initialization profiles still require dedicated typed controls, preflight rules,
smoke budgets, and output checks. They should be added to the same analysis
registry rather than exposed as arbitrary command text.
