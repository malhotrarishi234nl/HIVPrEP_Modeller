"""Shared, concise model and methods language for collaborator-facing outputs."""

from __future__ import annotations


MODEL_INTRO = (
    "This is a discrete-time, stochastic, population-based model of HIV transmission "
    "and prevention in the Netherlands. The current setup advances the population in "
    "quarterly steps, represents demographic and HIV-care processes in population "
    "strata, and uses repeated random seeds to show simulator variability."
)

MODEL_SCOPE = (
    "The model follows population change, sexual debut, HIV transmission and disease "
    "progression, diagnosis, ART, mortality, migration and optional PrEP. It is not an "
    "individual- or agent-based simulation."
)

HISTORY_MATCHING_INTRO = (
    "History matching is a parameter-space exclusion method. It asks which complete "
    "parameter vectors are not yet ruled out by the declared targets and uncertainty "
    "budget; it does not assign posterior probabilities or prove that a surviving "
    "vector is correct."
)

HISTORY_MATCHING_COMPONENTS = (
    (
        "Joint parameter designs",
        "Complete parameter vectors are evaluated together using space-filling designs, "
        "rather than tuning one coordinate at a time.",
    ),
    (
        "Stochastic replication",
        "Several seeds estimate simulator variation; fresh, disjoint seeds are used for "
        "confirmation so discovery noise is not mistaken for fit.",
    ),
    (
        "Target-level implausibility",
        "Each simulated target is compared with its observation using an explicit "
        "variance budget. The worst target controls the primary rule.",
    ),
    (
        "Iterative NROY refinement",
        "Later waves focus on the joint region that is Not Ruled Out Yet while retaining "
        "complete vectors and their target-level diagnostics.",
    ),
)

VALIDATION_NOTE = (
    "Calibration targets determine history-matching exclusion. Cross-validation series "
    "are reported separately and do not enlarge the NROY set. Software tests, input "
    "hashes and provenance checks establish mechanical reproducibility, not scientific fit."
)

PARAMETER_FAMILIES = (
    {
        "family": "Initialization and demography",
        "controls": "Starting population, age/sex structure, HIV seed, MSM share, fertility, mortality and migration",
        "role": "Defines who is present and how the population changes",
        "interface treatment": "Fixed initialization bundle; demographic schedules continue to operate",
    },
    {
        "family": "Contact and transmission",
        "controls": "Activity mixing, route-specific transmission probabilities and disease-stage multipliers",
        "role": "Determines opportunities for and probability of HIV transmission",
        "interface treatment": "Reviewed sensitivity",
    },
    {
        "family": "Disease progression and survival",
        "controls": "Acute→Chronic progression, Chronic→AIDS with and without ART, AIDS mortality and ART mortality effect",
        "role": "Moves people through HIV stages and determines survival",
        "interface treatment": "Reviewed sensitivity; changing it does not retain the calibration claim",
    },
    {
        "family": "Diagnosis and treatment",
        "controls": "Diagnosis pathways and schedules, ART initiation and discontinuation",
        "role": "Moves people into diagnosed and treated states",
        "interface treatment": "Diagnosis comparison available now; broader controls pending",
    },
    {
        "family": "Prevention policy",
        "controls": "PrEP eligibility, uptake, efficacy, persistence and intervention timing",
        "role": "Defines prevention scenarios; not every control is active in every run",
        "interface treatment": "Policy input",
    },
    {
        "family": "Run controls",
        "controls": "Start/end year, timestep, stochastic seed and number of runs",
        "role": "Controls execution and reproducibility; these are not fitted biological parameters",
        "interface treatment": "Run setting, subject to scenario-specific checks",
    },
)

SCENARIO_LEVERS = (
    {
        "area": "Diagnosis pathway",
        "examples": "M0 routine diagnosis; M1 AIDS-symptom diagnosis probability",
        "workbench status": "Available now",
    },
    {
        "area": "Disease progression",
        "examples": "Acute→Chronic; Chronic→AIDS off ART; Chronic→AIDS on ART",
        "workbench status": "Implemented in the model; guided sensitivity controls are next",
    },
    {
        "area": "Prevention policy",
        "examples": "PrEP eligibility, uptake, efficacy, persistence and timing",
        "workbench status": "Implemented in the model; guided policy controls are pending",
    },
    {
        "area": "Population initialization",
        "examples": "Starting population, age/sex structure and epidemic seed",
        "workbench status": "Kept as one fixed, matched bundle for ordinary experiments",
    },
    {
        "area": "Costs and QALYs",
        "examples": "Unit costs, utilities, discounting and policy comparison",
        "workbench status": "Economic code exists; browser workflow is pending",
    },
)

KEY_TERMS = (
    {
        "term": "M0",
        "meaning": "The standard routine-diagnosis pathway.",
    },
    {
        "term": "M1",
        "meaning": "M0 plus an annual chance of diagnosis for an undiagnosed person in the AIDS stage.",
    },
    {
        "term": "p",
        "meaning": "The added annual AIDS-symptom diagnosis probability used by M1; it is not a general late-diagnosis rate.",
    },
    {
        "term": "Seed",
        "meaning": "A reproducible random-number stream for one stochastic trajectory; it is not a parameter-vector ID.",
    },
    {
        "term": "NROY",
        "meaning": "Not Ruled Out Yet: parameter vectors surviving the declared history-matching rule.",
    },
)

FORMULAS = (
    (
        "Target implausibility",
        "I_i(x) = |mean[f_i(x)] - z_i| / sqrt(V_obs,i + V_code,i + V_ensemble,i + V_disc,i)",
    ),
    (
        "Primary history-matching rule",
        "I_max(x) = max_i I_i(x); NROY = {x : I_max(x) <= 3}",
    ),
    (
        "Annual event flow",
        "Y_year = sum of the within-year timestep events",
    ),
    (
        "Annual stock",
        "S_year = the final recorded stock value in that calendar year",
    ),
)

REFERENCES = (
    {
        "label": "Andrianakis et al. (2015), Bayesian history matching of complex infectious-disease models",
        "url": "https://doi.org/10.1371/journal.pcbi.1003968",
    },
    {
        "label": "Andrianakis et al. (2017), HIV history matching with variance emulation",
        "url": "https://doi.org/10.1111/rssc.12198",
    },
)
