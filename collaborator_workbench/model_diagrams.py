"""Small, code-native diagrams shared by the workbench and offline reports."""

from __future__ import annotations


_STYLE = """
  <style>
    .canvas { fill: #ffffff; }
    .title { fill: #17202a; font: 600 22px system-ui, sans-serif; }
    .label { fill: #17202a; font: 600 16px system-ui, sans-serif; }
    .small { fill: #46515c; font: 14px system-ui, sans-serif; }
    .tiny { fill: #5f6b76; font: 12px system-ui, sans-serif; }
    .node { stroke: #52606d; stroke-width: 1.5; rx: 12; }
    .neutral { fill: #edf2f7; }
    .susceptible { fill: #ddf4ec; }
    .infected { fill: #fff0c7; }
    .advanced { fill: #ffe0dc; }
    .care { fill: #e3edff; }
    .prevention { fill: #e5f7fb; }
    .arrow { fill: none; stroke: #52606d; stroke-width: 2; marker-end: url(#arrow); }
    .modifier { fill: none; stroke: #5271a8; stroke-width: 1.8; stroke-dasharray: 6 5; marker-end: url(#arrow-blue); }
    .guide { fill: none; stroke: #9aa5b1; stroke-width: 1.2; stroke-dasharray: 4 5; }
    @media (prefers-color-scheme: dark) {
      .canvas { fill: #10141b; }
      .title, .label { fill: #f5f7fa; }
      .small { fill: #d5dbe3; }
      .tiny { fill: #aeb8c4; }
      .node { stroke: #aeb8c4; }
      .neutral { fill: #28313d; }
      .susceptible { fill: #173d35; }
      .infected { fill: #4a3b15; }
      .advanced { fill: #4d2825; }
      .care { fill: #203657; }
      .prevention { fill: #173a43; }
      .arrow { stroke: #aeb8c4; }
      .modifier { stroke: #8eb4f0; }
      .guide { stroke: #697585; }
    }
  </style>
"""


DISEASE_PATHWAY_SVG = f"""
<svg viewBox="0 0 1100 430" width="100%" role="img" aria-labelledby="path-title path-desc"
     xmlns="http://www.w3.org/2000/svg">
  <title id="path-title">HIV disease progression and care pathway</title>
  <desc id="path-desc">Susceptible people may use PrEP. Infection moves them to acute HIV, then chronic HIV and AIDS. Diagnosis may occur in any infected stage, and diagnosed people may start ART. ART modifies transmission, progression and mortality.</desc>
  {_STYLE}
  <defs>
    <marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#52606d"/></marker>
    <marker id="arrow-blue" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#5271a8"/></marker>
  </defs>
  <rect class="canvas" x="1" y="1" width="1098" height="428" rx="14"/>
  <text class="title" x="40" y="42">Disease progression and care</text>
  <text class="small" x="40" y="68">A compact view of the implemented transitions; demographic movement occurs around this disease-state system.</text>

  <rect class="node susceptible" x="55" y="120" width="190" height="74"/>
  <text class="label" x="150" y="150" text-anchor="middle">Susceptible</text>
  <text class="small" x="150" y="175" text-anchor="middle">No PrEP or on PrEP</text>

  <rect class="node infected" x="320" y="120" width="170" height="74"/>
  <text class="label" x="405" y="162" text-anchor="middle">Acute HIV</text>

  <rect class="node infected" x="575" y="120" width="170" height="74"/>
  <text class="label" x="660" y="162" text-anchor="middle">Chronic HIV</text>

  <rect class="node advanced" x="830" y="120" width="170" height="74"/>
  <text class="label" x="915" y="162" text-anchor="middle">AIDS</text>

  <path class="arrow" d="M245 157 H320"/>
  <text class="small" x="282" y="141" text-anchor="middle">infection</text>
  <path class="arrow" d="M490 157 H575"/>
  <text class="small" x="532" y="141" text-anchor="middle">progression</text>
  <path class="arrow" d="M745 157 H830"/>
  <text class="small" x="787" y="131" text-anchor="middle">progression</text>
  <text class="tiny" x="787" y="151" text-anchor="middle">rate differs on/off ART</text>

  <rect class="node prevention" x="55" y="255" width="190" height="70"/>
  <text class="label" x="150" y="284" text-anchor="middle">PrEP</text>
  <text class="small" x="150" y="307" text-anchor="middle">reduces infection risk</text>
  <path class="modifier" d="M150 255 V206"/>

  <path class="guide" d="M320 225 H1000"/>
  <path class="guide" d="M405 194 V225 M660 194 V225 M915 194 V225"/>
  <text class="tiny" x="660" y="245" text-anchor="middle">Diagnosis can occur in Acute, Chronic or AIDS</text>

  <rect class="node care" x="330" y="275" width="185" height="70"/>
  <text class="label" x="422" y="304" text-anchor="middle">Undiagnosed</text>
  <text class="small" x="422" y="327" text-anchor="middle">routine pathway</text>
  <rect class="node care" x="605" y="275" width="185" height="70"/>
  <text class="label" x="697" y="304" text-anchor="middle">Diagnosed</text>
  <text class="small" x="697" y="327" text-anchor="middle">same HIV stage</text>
  <rect class="node care" x="880" y="275" width="165" height="70"/>
  <text class="label" x="962" y="304" text-anchor="middle">On ART</text>
  <text class="small" x="962" y="327" text-anchor="middle">dropout is possible</text>
  <path class="arrow" d="M515 310 H605"/>
  <text class="tiny" x="560" y="294" text-anchor="middle">diagnosis</text>
  <path class="arrow" d="M790 310 H880"/>
  <text class="tiny" x="835" y="294" text-anchor="middle">ART initiation</text>

  <text class="small" x="40" y="392">M1 changes one route: it adds symptom-triggered diagnosis for undiagnosed people in the AIDS stage.</text>
  <text class="tiny" x="40" y="414">ART modifies infectiousness, Chronic→AIDS progression and mortality; it is not a recovered state.</text>
</svg>
"""


STATE_SPACE_SVG = f"""
<svg viewBox="0 0 1100 350" width="100%" role="img" aria-labelledby="space-title space-desc"
     xmlns="http://www.w3.org/2000/svg">
  <title id="space-title">Population state-space architecture</title>
  <desc id="space-desc">The model crosses demographic and behavioral strata with HIV stage, diagnosis, ART and PrEP states, then updates counts every quarter.</desc>
  {_STYLE}
  <defs>
    <marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#52606d"/></marker>
  </defs>
  <rect class="canvas" x="1" y="1" width="1098" height="348" rx="14"/>
  <text class="title" x="40" y="42">How the population state is represented</text>
  <text class="small" x="40" y="68">Counts are stored across several axes. Not every mathematical combination is reachable.</text>

  <text class="label" x="180" y="112" text-anchor="middle">Population stratum</text>
  <rect class="node neutral" x="55" y="135" width="250" height="46"/>
  <text class="small" x="180" y="164" text-anchor="middle">20 adult age groups</text>
  <rect class="node neutral" x="55" y="196" width="250" height="46"/>
  <text class="small" x="180" y="225" text-anchor="middle">3 sex/orientation groups</text>
  <rect class="node neutral" x="55" y="257" width="250" height="46"/>
  <text class="small" x="180" y="286" text-anchor="middle">3 sexual-activity groups</text>

  <text class="title" x="350" y="226">×</text>

  <text class="label" x="535" y="112" text-anchor="middle">Health and intervention state</text>
  <rect class="node infected" x="410" y="135" width="250" height="46"/>
  <text class="small" x="535" y="164" text-anchor="middle">4 HIV stages</text>
  <rect class="node care" x="410" y="196" width="250" height="46"/>
  <text class="small" x="535" y="225" text-anchor="middle">2 diagnosis × 2 ART states</text>
  <rect class="node prevention" x="410" y="257" width="250" height="46"/>
  <text class="small" x="535" y="286" text-anchor="middle">Scenario-dependent PrEP states</text>

  <path class="arrow" d="M690 220 H770"/>
  <text class="tiny" x="730" y="202" text-anchor="middle">quarterly update</text>

  <text class="label" x="915" y="112" text-anchor="middle">Processes applied</text>
  <rect class="node susceptible" x="790" y="135" width="250" height="168"/>
  <text class="small" x="915" y="169" text-anchor="middle">Demography and migration</text>
  <text class="small" x="915" y="199" text-anchor="middle">Contact and transmission</text>
  <text class="small" x="915" y="229" text-anchor="middle">Progression and mortality</text>
  <text class="small" x="915" y="259" text-anchor="middle">Diagnosis, ART and PrEP</text>
  <text class="tiny" x="915" y="286" text-anchor="middle">stochastic event counts by seed</text>

  <text class="tiny" x="40" y="332">Population initialization is a coordinated bundle; progression, diagnosis, ART and prevention assumptions can be examined separately through bounded scenario controls.</text>
</svg>
"""


ANALYSIS_WORKFLOW_SVG = f"""
<svg viewBox="0 0 1100 365" width="100%" role="img" aria-labelledby="workflow-title workflow-desc"
     xmlns="http://www.w3.org/2000/svg">
  <title id="workflow-title">From historical fit to policy results</title>
  <desc id="workflow-desc">After scientific approval, a fitted historical trajectory supplies one saved state. Baseline and intervention scenarios start from that same state, generate paired epidemic outcomes, and then feed the economic comparison.</desc>
  {_STYLE}
  <defs>
    <marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#52606d"/></marker>
  </defs>
  <rect class="canvas" x="1" y="1" width="1098" height="363" rx="14"/>
  <text class="title" x="40" y="42">From fitted history to policy results</text>
  <text class="small" x="40" y="68">The underlying model already contains the scenario and economic layers; guided workbench controls are being added.</text>

  <rect class="node neutral" x="45" y="135" width="180" height="74"/>
  <text class="label" x="135" y="165" text-anchor="middle">Historical fit</text>
  <text class="tiny" x="135" y="188" text-anchor="middle">reviewed trajectory</text>
  <path class="arrow" d="M225 172 H300"/>

  <rect class="node care" x="300" y="135" width="190" height="74"/>
  <text class="label" x="395" y="165" text-anchor="middle">Saved state</text>
  <text class="tiny" x="395" y="188" text-anchor="middle">shared starting point</text>

  <path class="arrow" d="M490 158 C535 158 530 113 575 113"/>
  <path class="arrow" d="M490 186 C535 186 530 238 575 238"/>
  <rect class="node neutral" x="575" y="76" width="185" height="74"/>
  <text class="label" x="667" y="106" text-anchor="middle">Baseline arm</text>
  <text class="tiny" x="667" y="129" text-anchor="middle">current-policy scenario</text>
  <rect class="node prevention" x="575" y="201" width="185" height="74"/>
  <text class="label" x="667" y="231" text-anchor="middle">Intervention arm</text>
  <text class="tiny" x="667" y="254" text-anchor="middle">changed assumptions</text>

  <path class="arrow" d="M760 113 C805 113 800 158 845 158"/>
  <path class="arrow" d="M760 238 C805 238 800 186 845 186"/>
  <rect class="node infected" x="845" y="135" width="210" height="74"/>
  <text class="label" x="950" y="165" text-anchor="middle">Paired outcomes</text>
  <text class="tiny" x="950" y="188" text-anchor="middle">epidemic effects, costs, QALYs, NMB</text>

  <text class="small" x="40" y="318">Both arms use the same fitted starting state. Compatible random-number streams reduce unrelated stochastic noise in policy differences.</text>
  <text class="tiny" x="40" y="342">Conceptual workflow only: forecast and economic claims still depend on the selected backbone, assumptions and review status.</text>
</svg>
"""
