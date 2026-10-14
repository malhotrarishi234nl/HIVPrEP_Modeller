import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = "/Users/rishi/On Prem/OP Desk/Erasmus Folder/Econ - Thesis /hiv-prep-model";
const outDir = `${root}/outputs/professor_evidence_package_20260929`;
const payload = JSON.parse(await fs.readFile(`${outDir}/packet_data.json`, "utf8"));
const wb = Workbook.create();

const blue = "#404040";
const charcoal = "#404040";
const yellow = "#FFF200";
const magenta = "#C060A8";
const green = "#548235";
const gray = "#F2F2F2";
const border = "#D9D9D9";

function scalar(v) {
  if (v === null || v === undefined || v === "") return null;
  if (v === "True") return true;
  if (v === "False") return false;
  if (/^-?\d+(\.\d+)?([eE][+-]?\d+)?$/.test(String(v))) return Number(v);
  return v;
}

function styleTitle(sheet, lastCol) {
  const title = sheet.getRange(`A2:${lastCol}2`);
  title.format.font = { name: "Arial", size: 14, bold: true, color: "#000000" };
  sheet.getRange(`A3:${lastCol}3`).format.font = { name: "Arial", size: 9, italic: true, color: "#666666" };
}

function applyTableStyle(sheet, headerRow, lastRow, lastCol) {
  const hdr = sheet.getRange(`A${headerRow}:${lastCol}${headerRow}`);
  hdr.format.fill = blue;
  hdr.format.font = { name: "Arial", size: 9, bold: true, color: "#FFFFFF" };
  hdr.format.horizontalAlignment = "center";
  hdr.format.verticalAlignment = "center";
  hdr.format.wrapText = true;
  hdr.format.borders = { preset: "all", style: "thin", color: "#FFFFFF" };
  const body = sheet.getRange(`A${headerRow + 1}:${lastCol}${lastRow}`);
  body.format.font = { name: "Arial", size: 9, color: "#222222" };
  body.format.verticalAlignment = "center";
  body.format.borders = { insideHorizontal: { style: "thin", color: border } };
  for (let r = headerRow + 1; r <= lastRow; r += 2) sheet.getRange(`A${r}:${lastCol}${r}`).format.fill = gray;
  body.format.autofitColumns();
  body.format.autofitRows();
  sheet.getRange(`A1:${lastCol}${lastRow}`).format.rowHeight = 18;
  hdr.format.rowHeight = 30;
  sheet.freezePanes.freezeRows(headerRow);
  sheet.showGridLines = false;
}

function num(v) { return v === null || v === undefined || v === "" ? null : Number(v); }

function buildCalibrationResults() {
  const oldRows = payload.earlier_target_fit.rows;
  const newRows = payload.new_target_fit.rows;
  const old = new Map(oldRows.map(r => [`${r.tuple_id}|${r.target}`, r]));
  const entries = newRows.map(r => {
    const target = r.target_key;
    const legacy = old.get(`D_1788_v000|${target}`);
    const delayed = old.get(`D_1788_v109|${target}`);
    const observed = num(r.break_2008__observed_z);
    const y = Number(target.split("@").pop());
    const values = [
      num(legacy?.primary_implausibility), num(delayed?.primary_implausibility),
      num(r.break_2008__confirmation_primary_implausibility), num(r.break_2010__confirmation_primary_implausibility),
    ];
    const names = ["Earlier legacy", "Earlier delayed diagnosis", "2008 break", "2010 break"];
    return [target, y, observed,
      num(legacy?.model_mean), num(legacy?.model_sd), values[0],
      num(delayed?.model_mean), num(delayed?.model_sd), values[1],
      num(r.break_2008__confirmation_mean), Math.sqrt(num(r.break_2008__confirmation_sample_variance)), values[2],
      num(r.break_2010__confirmation_mean), Math.sqrt(num(r.break_2010__confirmation_sample_variance)), values[3],
      names[values.indexOf(Math.min(...values))],
    ];
  }).sort((a,b) => a[1]-b[1] || String(a[0]).localeCompare(String(b[0])));
  const headers = ["Target ID","Year","Observed","Earlier legacy mean","Earlier legacy SD","Earlier legacy I","Earlier delayed mean","Earlier delayed SD","Earlier delayed I","2008 break mean","2008 break SD","2008 break I","2010 break mean","2010 break SD","2010 break I","Closest configuration"];
  const sheet = wb.worksheets.add("Calibration results");
  sheet.getRange("A2").values = [["Calibration results by target-year"]];
  sheet.getRange("A3").values = [["Model means and SDs are over confirmation seeds. Implausibility is uncertainty-standardised and is not a percentage."]];
  sheet.getRange("A5").write([headers, ...entries]);
  styleTitle(sheet, "P"); applyTableStyle(sheet, 5, 5 + entries.length, "P");
  sheet.getRange("A5:P5").format.fill = charcoal;
  sheet.getRange("G5:I5").format.font = { name:"Arial", size:9, bold:true, color:magenta };
  sheet.getRange("C6:C18").format.fill = yellow; sheet.getRange("C6:C18").format.font = { name:"Arial", size:9, bold:true, color:"#000000" };
  for (const c of ["F","I","L","O"]) {
    sheet.getRange(`${c}6:${c}18`).format.font = { name:"Arial", size:9, color:green };
    sheet.getRange(`${c}6:${c}18`).setNumberFormat("0.00");
  }
  for (const c of ["D","E","G","H","J","K","M","N"]) sheet.getRange(`${c}6:${c}18`).setNumberFormat("0.00");
  sheet.getRange("C6:C18").setNumberFormat("0");
  sheet.getRange("A:A").format.columnWidth = 33; sheet.getRange("B:C").format.columnWidth = 10;
  sheet.getRange("D:O").format.columnWidth = 16; sheet.getRange("P:P").format.columnWidth = 23;
  sheet.getRange("A6:A18").format.font = { name:"Arial", size:9, color:"#222222" };
  sheet.showGridLines = false; sheet.freezePanes.freezeRows(5);
  return sheet;
}

buildCalibrationResults();

function colName(n) {
  let s = "";
  while (n > 0) { n--; s = String.fromCharCode(65 + n % 26) + s; n = Math.floor(n / 26); }
  return s;
}

function addDataSheet(name, key, title, selectedHeaders = null) {
  const ds = payload[key];
  const sourceRows = ds.rows;
  const headers = selectedHeaders || (sourceRows.length ? Object.keys(sourceRows[0]) : ["No records"]);
  const matrix = [headers, ...sourceRows.map(r => headers.map(h => scalar(r[h])))];
  const sheet = wb.worksheets.add(name);
  const lastCol = colName(headers.length);
  sheet.getRange("A2").values = [[title]];
  sheet.getRange("A3").values = [[`Source: ${ds.source}`]];
  sheet.getRange("A5").write(matrix);
  styleTitle(sheet, lastCol);
  applyTableStyle(sheet, 5, 5 + sourceRows.length, lastCol);
  sheet.getRange(`A5:${lastCol}${5 + sourceRows.length}`).format.wrapText = false;
  sheet.getRange("A:A").format.columnWidth = 26;
  return sheet;
}

// Executive summary
const summary = wb.worksheets.add("Executive summary");
summary.tabColor = blue; summary.showGridLines = false;
summary.getRange("A2").values = [["Updated model run verification"]];
summary.getRange("A2").format.font = { name: "Arial", size: 16, bold: true, color: "#000000" };
summary.getRange("A3").values = [["Verified VM suite completed 29 September 2026 | 2008-break primary | 2010-break structural sensitivity"]];
summary.getRange("A3").format.font = { name: "Arial", size: 9, italic: true, color: "#666666" };
const cal = Object.fromEntries(payload.calibration.rows.map(r => [r.case_id, r]));
const p08 = Object.fromEntries(payload.policy_2008.rows.map(r => [r.scenario, r]));
const psa08 = Object.fromEntries(payload.psa_2008.rows.map(r => [r.scenario, r]));
const cards = [
  ["Run status", "COMPLETE", "Validation record reports no retained failure artifacts"],
  ["2008-break maximum I", Number(cal.break_2008.confirmation_primary_i_max), "Below the frozen cutoff of 3"],
  ["2010-break maximum I", Number(cal.break_2010.confirmation_primary_i_max), "Below the frozen cutoff of 3"],
  ["LENA add-on 1,500 policy NMB (EUR m)", Number(p08.scenario_lena_add_on__add_on_1500.mean_nmb) / 1e6, "15 fixed-parameter paired seeds"],
  ["LENA add-on 1,500 PSA P(CE)", Number(psa08.scenario_lena_add_on__add_on_1500.probability_cost_effective), "1,000 parameter draws"],
  ["Oral +3,000 PSA mean NMB (EUR m)", Number(psa08.scenario_oral_matched_add_3000.mean_nmb) / 1e6, "Positive mean; P(CE) remains about 30%"],
];
summary.getRange("A5:C5").values = [["Headline measure", "Value", "Interpretation"]];
summary.getRange("A6").write(cards);
applyTableStyle(summary, 5, 5 + cards.length, "C");
summary.getRange("A:A").format.columnWidth = 38; summary.getRange("B:B").format.columnWidth = 18; summary.getRange("C:C").format.columnWidth = 48;
summary.getRange("B7:B8").setNumberFormat("0.00");
summary.getRange("B9:B9").setNumberFormat("0.0");
summary.getRange("B10:B10").setNumberFormat("0.0%");
summary.getRange("B11:B11").setNumberFormat("0.0");
summary.getRange("A14").values = [["Interpretation"]]; summary.getRange("A14").format.font = { name: "Arial", size: 11, bold: true, color: "#000000" };
summary.getRange("A15:C18").values = [["Both selected calibration candidates passed the prespecified target contract.", null, null],["All tested lenacapavir policies had negative mean NMB in the 15-seed policy analysis.", null, null],["The 1,000-draw PSA shows wide uncertainty but does not reverse the mean lenacapavir conclusion.", null, null],["The workbook keeps policy-seed and PSA-draw evidence separate because they represent different uncertainty layers.", null, null]];
summary.getRange("A15:A18").format.font = { name: "Arial", size: 10, color: "#222222" };

addDataSheet("Calibration", "calibration", "Selected calibration candidates");
addDataSheet("Policy summary 2008", "policy_2008", "Policy analysis summary for 2008-break primary");
addDataSheet("Policy summary 2010", "policy_2010", "Policy analysis summary for 2010-break sensitivity");
addDataSheet("PSA summary 2008", "psa_2008", "Probabilistic sensitivity analysis for 2008-break primary");
addDataSheet("PSA summary 2010", "psa_2010", "Probabilistic sensitivity analysis for 2010-break sensitivity");
addDataSheet("Structural policy", "paired_policy", "Paired 2008-minus-2010 policy differences");
addDataSheet("Structural PSA", "paired_psa", "Paired 2008-minus-2010 PSA differences");
addDataSheet("Policy seeds 2008", "policy_seed_2008", "Seed-level policy records for 2008-break primary");
addDataSheet("Policy seeds 2010", "policy_seed_2010", "Seed-level policy records for 2010-break sensitivity");
addDataSheet("PSA draws 2008", "psa_draw_2008", "Draw-level PSA records for 2008-break primary");
addDataSheet("PSA draws 2010", "psa_draw_2010", "Draw-level PSA records for 2010-break sensitivity");
addDataSheet("Convergence 2008", "convergence_2008", "PSA convergence diagnostics for 2008-break primary");
addDataSheet("Convergence 2010", "convergence_2010", "PSA convergence diagnostics for 2010-break sensitivity");

const provenance = wb.worksheets.add("Provenance"); provenance.tabColor = "#7F8C8D"; provenance.showGridLines = false;
provenance.getRange("A2").values = [["Evidence sources and interpretation rules"]]; styleTitle(provenance, "D");
const provRows = Object.entries(payload).map(([k,v]) => [k, v.source, v.rows.length, "Imported without altering source records"]);
provenance.getRange("A5").write([["Dataset", "Verified restore path", "Rows", "Use"], ...provRows]);
applyTableStyle(provenance, 5, 5 + provRows.length, "D");
provenance.getRange("A:A").format.columnWidth = 25; provenance.getRange("B:B").format.columnWidth = 90; provenance.getRange("C:C").format.columnWidth = 10; provenance.getRange("D:D").format.columnWidth = 36;
provenance.getRange("A22:D25").values = [["Rule", "15-seed policy analysis", "Fixed parameters; stochastic simulation uncertainty", null],["Rule", "1,000-draw PSA", "Parameter uncertainty plus modeled stochastic variation", null],["Rule", "Calibration compatibility", "Not predictive validation or proof of true structure", null],["Rule", "NMB threshold", "EUR 80,000 per QALY in supplied outputs", null]];

await wb.recalculate();
const errors = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, maxChars: 6000 });
await fs.writeFile(`${outDir}/workbook_formula_check_professor_style.txt`, errors.ndjson || String(errors));
const preview = await wb.render({ sheetName: "Calibration results", range: "A1:P19", scale: 1.15, format: "png" });
await fs.writeFile(`${outDir}/workbook_preview_professor_style.png`, new Uint8Array(await preview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(`${outDir}/Updated_Model_Run_Verification_Professor_Style.xlsx`);
