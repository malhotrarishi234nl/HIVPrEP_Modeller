import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = new URL("../outputs/hiv_diagnoses_excel/", import.meta.url);
const outputPath = new URL("hiv_1_diagnoses_table_1_1.xlsx", outputDir);
const previewPath = new URL("hiv_1_diagnoses_table_1_1_preview.png", outputDir);
const dataPreviewPath = new URL("hiv_1_diagnoses_data_preview.png", outputDir);

const rows = [
  ["<=1995", 2095, null, 719, null, 561, null, 15, null, 53, null, 3443, null],
  ["1996", 367, null, 159, null, 100, null, 3, null, 10, null, 639, null],
  ["1997", 421, null, 188, null, 138, null, 5, null, 11, null, 763, null],
  ["1998", 317, null, 155, null, 126, null, 1, null, 11, null, 610, null],
  ["1999", 332, null, 159, null, 150, null, 5, null, 13, null, 659, null],
  ["2000", 355, null, 205, null, 201, null, 5, null, 15, null, 781, null],
  ["2001", 425, null, 230, null, 239, null, 7, null, 17, null, 918, null],
  ["2002", 447, null, 250, null, 265, null, 6, null, 15, null, 983, null],
  ["2003", 427, null, 257, null, 278, null, 10, null, 21, null, 993, null],
  ["2004", 563, null, 263, null, 269, null, 9, null, 13, null, 1117, null],
  ["2005", 604, null, 262, null, 259, null, 10, null, 11, null, 1146, null],
  ["2006", 631, null, 223, null, 204, null, 6, null, 4, null, 1068, null],
  ["2007", 744, null, 213, null, 208, null, 9, null, 4, null, 1178, null],
  ["2008", 814, null, 223, null, 175, null, 12, null, 9, null, 1233, null],
  ["2009", 749, null, 219, null, 177, null, 9, null, 6, null, 1160, null],
  ["2010", 753, null, 214, null, 164, null, 8, null, 8, null, 1147, null],
  ["2011", 746, null, 201, null, 149, null, 10, null, 1, null, 1107, null],
  ["2012", 688, null, 186, null, 153, null, 12, null, 3, null, 1042, null],
  ["2013", 722, null, 164, null, 124, null, 10, null, 1, null, 1021, null],
  ["2014", 599, null, 156, null, 128, null, 7, null, 2, null, 892, null],
  ["2015", 567, null, 172, null, 126, null, 15, null, 1, null, 881, null],
  ["2016", 528, null, 146, null, 104, null, 5, null, 2, null, 785, null],
  ["2017", 502, 502, 144, 144, 95, 95, 12, 12, 1, 1, 754, 754],
  ["2018", 444, 444, 132, 132, 82, 82, 16, 16, 1, 1, 675, 675],
  ["2019", 348, 350, 143, 144, 107, 107, 18, 18, 1, 1, 617, 620],
  ["2020", 257, 260, 90, 91, 76, 77, 14, 14, 0, 0, 437, 442],
  ["2021", 240, 245, 99, 102, 67, 69, 13, 13, 1, 1, 420, 430],
  ["2022", 234, 242, 107, 112, 82, 86, 15, 16, 0, 0, 438, 456],
  ["2023", 256, 271, 119, 130, 75, 82, 14, 15, 0, 0, 464, 498],
  ["2024", 267, 301, 91, 111, 72, 88, 14, 17, 0, 0, 444, 517],
  ["Total", 16442, 16508, 5889, 5930, 4954, 4985, 295, 301, 235, 235, 27815, 27960],
];

const flatHeaders = [
  "Year of diagnosis",
  "MSM reported",
  "MSM adjusted",
  "Other men reported",
  "Other men adjusted",
  "Women reported",
  "Women adjusted",
  "Trans men and women reported",
  "Trans men and women adjusted",
  "<15 years of age reported",
  "<15 years of age adjusted",
  "Total reported",
  "Total adjusted",
];

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("Table 1.1");
const dataSheet = workbook.worksheets.add("Data");

const title =
  "Table 1.1: Annual number of HIV-1 diagnoses among men who have sex with men (MSM), other men, women,\n" +
  "trans men and women, and children below 15 years of age. Numbers in the second column for each group are\n" +
  "adjusted to reflect a delay in notification to SHM and due to rounding may not add up to the total number\n" +
  "reported in the last column.";

sheet.getRange("A1:M4").merge();
sheet.getRange("A1").values = [[title]];
sheet.getRange("A1:M4").format = {
  fill: "#FFFFFF",
  font: { name: "Aptos", size: 11, bold: true, italic: true, color: "#FF0000" },
  wrapText: true,
  verticalAlignment: "center",
};
sheet.getRange("A1:M4").format.rowHeightPx = 24;

sheet.getRange("A6:M6").format.borders = { preset: "top", style: "thin", color: "#FF0000" };
sheet.getRange("A6:A7").merge();
sheet.getRange("B6:C6").merge();
sheet.getRange("D6:E6").merge();
sheet.getRange("F6:G6").merge();
sheet.getRange("H6:I6").merge();
sheet.getRange("J6:K6").merge();
sheet.getRange("L6:M6").merge();

sheet.getRange("A6:M7").values = [
  [
    "Year of\ndiagnosis",
    "MSM",
    null,
    "Other men",
    null,
    "Women",
    null,
    "Trans men\nand women",
    null,
    "<15 years\nof age",
    null,
    "Total",
    null,
  ],
  [
    null,
    "Reported",
    "Adjusted",
    "Reported",
    "Adjusted",
    "Reported",
    "Adjusted",
    "Reported",
    "Adjusted",
    "Reported",
    "Adjusted",
    "Reported",
    "Adjusted",
  ],
];

sheet.getRange("A6:M7").format = {
  fill: "#E8EBF2",
  font: { name: "Aptos", size: 10, bold: true, color: "#00337F" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
};
sheet.getRange("A6:A7").format.horizontalAlignment = "left";
sheet.getRange("A7:M7").format.borders = { preset: "bottom", style: "thin", color: "#5D7EBB" };

sheet.getRange(`A8:M${8 + rows.length - 1}`).values = rows;
sheet.getRange(`A8:M${8 + rows.length - 1}`).format = {
  font: { name: "Aptos", size: 10, color: "#00337F" },
  horizontalAlignment: "right",
  verticalAlignment: "center",
};
sheet.getRange(`A8:A${8 + rows.length - 1}`).format.horizontalAlignment = "left";
sheet.getRange(`A8:M${8 + rows.length - 1}`).format.numberFormat = "#,##0";
sheet.getRange("A8:A37").format.numberFormat = "0";

for (const range of ["B8:C38", "F8:G38", "J8:K38"]) {
  sheet.getRange(range).format.fill = "#E9ECF4";
}
for (const range of ["D8:E38", "H8:I38", "L8:M38"]) {
  sheet.getRange(range).format.fill = "#F5F6FA";
}
sheet.getRange("A8:A38").format.fill = "#F5F6FA";

sheet.getRange("A38:M38").format = {
  fill: "#F5F6FA",
  font: { name: "Aptos", size: 10, bold: true, color: "#00337F" },
  horizontalAlignment: "right",
  verticalAlignment: "center",
};
sheet.getRange("B38:M38").format.numberFormat = "#,##0";
sheet.getRange("A38").format.horizontalAlignment = "left";
sheet.getRange("A38:M38").format.borders = { preset: "top", style: "thin", color: "#00337F" };
sheet.getRange("A39:M39").format.borders = { preset: "top", style: "thin", color: "#FF0000" };

sheet.getRange("A40:M40").merge();
sheet.getRange("A40").values = [["Legend: MSM = men who have sex with men."]];
sheet.getRange("A40:M40").format = {
  fill: "#FFFFFF",
  font: { name: "Aptos", size: 10, bold: true, italic: true, color: "#FF0000" },
};

sheet.getRange("A:A").format.columnWidthPx = 95;
sheet.getRange("B:M").format.columnWidthPx = 78;
sheet.getRange("A6:M7").format.rowHeightPx = 34;
sheet.getRange("A8:M38").format.rowHeightPx = 22;
sheet.freezePanes.freezeRows(7);
sheet.freezePanes.freezeColumns(1);

dataSheet.getRange("A1:M1").values = [flatHeaders];
dataSheet.getRange(`A2:M${rows.length + 1}`).values = rows;
dataSheet.getRange(`A1:M${rows.length + 1}`).format = {
  font: { name: "Aptos", size: 10, color: "#1F2937" },
  verticalAlignment: "center",
};
dataSheet.getRange("A1:M1").format = {
  fill: "#00337F",
  font: { name: "Aptos", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
};
dataSheet.getRange(`B2:M${rows.length + 1}`).format.numberFormat = "#,##0";
dataSheet.getRange("A:A").format.columnWidthPx = 115;
dataSheet.getRange("B:M").format.columnWidthPx = 115;
dataSheet.getRange("A1:M1").format.rowHeightPx = 42;
dataSheet.freezePanes.freezeRows(1);

const sample = await workbook.inspect({
  kind: "table",
  range: "Table 1.1!A6:M38",
  include: "values",
  tableMaxRows: 40,
  tableMaxCols: 13,
});
console.log(sample.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 50 },
  summary: "formula error scan",
});
console.log(errors.ndjson);

const preview = await workbook.render({ sheetName: "Table 1.1", range: "A1:M40", scale: 2 });
const dataPreview = await workbook.render({ sheetName: "Data", range: "A1:M32", scale: 2 });
await fs.mkdir(outputDir, { recursive: true });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
await fs.writeFile(dataPreviewPath, new Uint8Array(await dataPreview.arrayBuffer()));

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(`Saved ${outputPath.pathname}`);
