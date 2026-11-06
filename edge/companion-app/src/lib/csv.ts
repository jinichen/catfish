/** CSV / TSV 拆成行列 (右侧成果区的表格显示用)。支持引号里的逗号、换行和 "" 转义; 行数有上限, 超了只给前面的。 */
export function parseDelimited(text: string, delimiter: "," | "\t", maxRows = 2000): { rows: string[][]; truncated: boolean } {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  let i = 0;
  const pushRow = () => {
    row.push(cell);
    rows.push(row);
    row = [];
    cell = "";
  };
  while (i < text.length) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          cell += '"';
          i += 2;
          continue;
        }
        quoted = false;
      } else {
        cell += c;
      }
      i++;
      continue;
    }
    if (c === '"' && cell === "") {
      quoted = true;
    } else if (c === delimiter) {
      row.push(cell);
      cell = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      pushRow();
      if (rows.length >= maxRows) return { rows, truncated: i + 1 < text.length };
    } else {
      cell += c;
    }
    i++;
  }
  if (cell !== "" || row.length > 0) pushRow();
  return { rows, truncated: false };
}
