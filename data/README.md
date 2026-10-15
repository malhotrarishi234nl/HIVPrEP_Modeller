# External data contract

Small redistributable reference inputs may live under `data/reference/`.
Large, licensed, source-controlled elsewhere, or raw inputs remain outside Git
under the sibling `../data/raw/` tree.

The machine-readable active-input list is `external_inputs.json`. A collaborator
must verify every required SHA-256 before using an input for scientific claims.
Path matching without hash verification is insufficient.

The current robust-NROY target contract depends on
`../data/raw/validation/DiseaseData.xlsx`. Additional demographic and validation
workflows use the UNAIDS CSV, Human Mortality Database population file, and net
migration CSV listed in the manifest.

The literature library is similarly external at `../literature/`. Do not copy
or redistribute those PDFs until permissions are checked. The diagnosis
triangulation workflow carries exact filename/hash pins in its plan and code.

If roots become configurable, preserve logical relative paths and content
hashes. Environment-variable convenience must not weaken identity checking.
