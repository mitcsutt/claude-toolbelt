// Pipe-table parser for skill and reference markdown, so tests can check
// routing tables as data rather than grepping prose. No dependencies.
//
//   import { tables } from './skill-tables.mjs';
//   tables(markdown) -> [{ heading, header: [...], rows: [[...], ...] }]
//
// `heading` is the nearest preceding "## " heading (null before the first).
// Tables inside fenced code blocks are ignored. A cell may contain an
// escaped pipe (\|), which is kept as a literal "|".

const ROW = /^\s*\|.*\|\s*$/;
const SEPARATOR = /^\s*\|(\s*:?-{3,}:?\s*\|)+\s*$/;

function cells(line) {
  const inner = line.trim().slice(1, -1);
  const out = [];
  let cur = '';
  for (let i = 0; i < inner.length; i += 1) {
    if (inner[i] === '\\' && inner[i + 1] === '|') {
      cur += '|';
      i += 1;
    } else if (inner[i] === '|') {
      out.push(cur.trim());
      cur = '';
    } else {
      cur += inner[i];
    }
  }
  out.push(cur.trim());
  return out;
}

export function tables(markdown) {
  const lines = markdown.split('\n');
  const out = [];
  let heading = null;
  let fenced = false;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (/^\s*(```|~~~)/.test(line)) {
      fenced = !fenced;
      continue;
    }
    if (fenced) continue;
    const h = line.match(/^## (.+)$/);
    if (h) {
      heading = h[1].trim();
      continue;
    }
    if (ROW.test(line) && i + 1 < lines.length && SEPARATOR.test(lines[i + 1])) {
      const table = { heading, header: cells(line), rows: [] };
      i += 2;
      while (i < lines.length && ROW.test(lines[i])) {
        table.rows.push(cells(lines[i]));
        i += 1;
      }
      i -= 1;
      out.push(table);
    }
  }
  return out;
}

// Backticked names in a string, in order: "`a` then `b`" -> ['a', 'b'].
export function backticked(text) {
  return [...text.matchAll(/`([^`]+)`/g)].map((m) => m[1]);
}
