# HIV PrEP model

A stochastic model of HIV transmission and PrEP interventions in the
Netherlands, developed for Rishi Malhotra's MSc thesis and related research.

The repository includes the model code, a browser-based workbench, a historical
example, supporting documentation, and tests.

## Setup

Download the repository using GitHub's **Code > Download ZIP** button, or clone
it:

```bash
git clone https://github.com/malhotrarishi234nl/HIVPrEP_Modeller.git
cd HIVPrEP_Modeller
```

Install [Python 3.11](https://www.python.org/downloads/), then run the included
setup file once:

- **macOS:** double-click `Setup Workbench.command`
- **Windows:** double-click `Setup Workbench.bat`

After setup, start the workbench with:

- **macOS:** double-click `Launch Workbench.command`
- **Windows:** double-click `Launch Workbench.bat`

The workbench opens locally in your web browser. Setup creates an isolated
`.venv-workbench` environment inside the repository and does not modify the
system Python installation.

For terminal setup on macOS or Linux:

```bash
python3.11 -m venv .venv-workbench
.venv-workbench/bin/python -m pip install -r requirements-workbench.txt
.venv-workbench/bin/python -m streamlit run workbench_ui/app.py
```

## What is included

- **Browser workbench:** explore the model, review results, change supported
  inputs, run bounded analyses, compare outputs, and export reports.
- **Historical example:** a bundled 2008 result that can be opened immediately
  without running a new simulation.
- **Model code:** the stochastic simulation engine and guarded M0/M1 runner.
- **Configuration files:** model assumptions, targets, economic inputs, and
  supported parameter ranges.
- **Documentation and tests:** detailed methods, technical notes, and automated
  checks for the model and workbench.

Start on the workbench **Home** page and open the historical example. The
**Model guide** explains the model structure, while **Data and targets** shows
which supporting inputs are available on the current computer.

## Repository overview

```text
workbench_ui/           browser interface
model_workbench/        workbench services and command-line tools
collaborator_workbench/ result import, reports, diagrams, and Excel exports
examples/workbench/     bundled historical example
configs/                model and analysis configurations
data/reference/         small reference inputs included in Git
documentation/          methods, guides, and technical records
tests/                  automated tests
run_model.py            supported M0/M1 command-line runner
run_v3.py               core simulation engine
outputs/                local run output; ignored by Git
```

## Data and results

The bundled historical example is included in the repository. Some large or
restricted raw datasets and full historical run archives are stored separately
and are not downloaded from GitHub. The workbench reports missing external
inputs clearly; they are not required to browse the included example.

New local results are written under `outputs/`, which is excluded from Git.

## More information

- [Local workbench guide](documentation/guides/LOCAL_MODEL_WORKBENCH.md)
- [Documentation index](documentation/INDEX.md)
- [Configuration guide](configs/README.md)
- [Testing guide](documentation/guides/TESTING.md)
