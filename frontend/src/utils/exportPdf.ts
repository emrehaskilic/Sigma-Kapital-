/**
 * Sigma Kapital — Corporate Backtest PDF Report Generator
 */
import { jsPDF } from "jspdf";
import autoTable from "jspdf-autotable";
import type { BacktestResult, BacktestTrade } from "../types";
import { formatNum } from "../utils";

/* ── Colors ── */
const NAVY = [11, 18, 23] as const;       // #0b1217
const DARK_BG = [19, 29, 42] as const;    // #131d2a
const BLUE = [74, 144, 217] as const;     // #4A90D9
const SILVER = [160, 160, 160] as const;
const WHITE = [240, 240, 240] as const;
const GREEN = [16, 185, 129] as const;    // #10b981
const RED = [239, 68, 68] as const;       // #ef4444
const GRAY = [100, 116, 139] as const;    // #64748b
const LIGHT_GRAY = [200, 200, 200] as const;

const fmtDateTime = (ts: number) => {
  if (!ts) return "-";
  const d = new Date(ts);
  const yy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${yy}-${mm}-${dd} ${hh}:${mi}`;
};

function pnlColorRgb(v: number): readonly [number, number, number] {
  if (v > 0) return GREEN;
  if (v < 0) return RED;
  return GRAY;
}

/* ── Draw the SK logo using jsPDF primitives ── */
function drawLogo(doc: jsPDF, x: number, y: number, scale: number = 1) {
  const s = scale;

  // "S" letter — blue gradient approximated with solid blue
  doc.setFont("helvetica", "bold");
  doc.setFontSize(28 * s);
  doc.setTextColor(...BLUE);
  doc.text("S", x, y + 14 * s);

  // "K" letter — silver
  doc.setTextColor(...SILVER);
  doc.text("K", x + 17 * s, y + 14 * s);

  // Arrow going up-right from K
  doc.setDrawColor(...LIGHT_GRAY);
  doc.setLineWidth(1.5 * s);
  doc.line(x + 32 * s, y + 8 * s, x + 40 * s, y);
  // Arrow head
  doc.setFillColor(...LIGHT_GRAY);
  doc.triangle(
    x + 39 * s, y - 3 * s,
    x + 43 * s, y + 1 * s,
    x + 38 * s, y + 2 * s,
    "F"
  );

  // "SIGMA" text
  doc.setFontSize(12 * s);
  doc.setTextColor(...LIGHT_GRAY);
  doc.text("SIGMA", x + 46 * s, y + 5 * s);

  // "KAPITAL" text
  doc.setTextColor(...BLUE);
  doc.text("KAPITAL", x + 46 * s, y + 14 * s);
}

/* ── Section header ── */
function sectionHeader(doc: jsPDF, title: string, y: number): number {
  doc.setFillColor(...DARK_BG);
  doc.roundedRect(10, y, doc.internal.pageSize.getWidth() - 20, 9, 2, 2, "F");
  doc.setFont("helvetica", "bold");
  doc.setFontSize(10);
  doc.setTextColor(...BLUE);
  doc.text(title, 14, y + 6.5);
  return y + 13;
}

/* ── Check if we need a new page ── */
function checkPage(doc: jsPDF, y: number, needed: number = 30): number {
  if (y + needed > doc.internal.pageSize.getHeight() - 20) {
    doc.addPage();
    return drawPageFooter(doc, 15);
  }
  return y;
}

/* ── Page footer ── */
function drawPageFooter(doc: jsPDF, startY: number): number {
  const pageH = doc.internal.pageSize.getHeight();
  const pageW = doc.internal.pageSize.getWidth();

  // Bottom line
  doc.setDrawColor(...BLUE);
  doc.setLineWidth(0.3);
  doc.line(10, pageH - 12, pageW - 10, pageH - 12);

  // Footer text
  doc.setFont("helvetica", "normal");
  doc.setFontSize(7);
  doc.setTextColor(...GRAY);
  doc.text("Sigma Kapital Trading Technologies & Market Making Services", 10, pageH - 8);
  doc.text(`Sayfa ${doc.getNumberOfPages()}`, pageW - 10, pageH - 8, { align: "right" });
  doc.text("GIZLI — Sadece dahili kullanim icin", pageW / 2, pageH - 8, { align: "center" });

  return startY;
}

/* ── Metric row helper ── */
function metricRow(doc: jsPDF, y: number, label: string, value: string, color?: readonly [number, number, number]): number {
  doc.setFont("helvetica", "normal");
  doc.setFontSize(8.5);
  doc.setTextColor(...GRAY);
  doc.text(label, 14, y);
  doc.setFont("helvetica", "bold");
  doc.setTextColor(...(color || WHITE));
  doc.text(value, 90, y, { align: "right" });
  return y + 5;
}

/* ══════════════════════════════════════════════════════════════════════
   Main Export Function
   ══════════════════════════════════════════════════════════════════════ */
export function exportBacktestPdf(result: BacktestResult) {
  const m = result.metrics;
  const doc = new jsPDF({ orientation: "portrait", unit: "mm", format: "a4" });
  const pageW = doc.internal.pageSize.getWidth();

  // ── Page 1: Cover & Summary ────────────────────────────────────────

  // Dark background for header
  doc.setFillColor(...NAVY);
  doc.rect(0, 0, pageW, 50, "F");

  // Logo
  drawLogo(doc, 12, 10, 1.1);

  // Report title
  doc.setFont("helvetica", "bold");
  doc.setFontSize(16);
  doc.setTextColor(...WHITE);
  doc.text("Backtest Raporu", pageW - 14, 18, { align: "right" });

  // Date
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.setTextColor(...GRAY);
  doc.text(new Date().toLocaleString("tr-TR"), pageW - 14, 26, { align: "right" });

  // Subtitle
  doc.setFontSize(8);
  doc.setTextColor(...SILVER);
  doc.text(`${m.total_trades} islem | ${m.total_symbols} parite | ${m.leverage}x kaldirac`, pageW - 14, 34, { align: "right" });

  // Divider
  doc.setDrawColor(...BLUE);
  doc.setLineWidth(0.5);
  doc.line(10, 50, pageW - 10, 50);

  let y = 58;

  // ── Performans Ozeti ──
  y = sectionHeader(doc, "PERFORMANS OZETI", y);

  // Two-column layout
  const col1x = 14;
  const col2x = pageW / 2 + 5;

  // Left column
  let ly = y + 2;
  ly = metricRow(doc, ly, "Total PnL", `${formatNum(m.total_pnl, 2, true)} USDT`, pnlColorRgb(m.total_pnl));
  ly = metricRow(doc, ly, "Total PnL %", `${formatNum(m.total_pnl_pct, 2, true)}%`, pnlColorRgb(m.total_pnl_pct));
  ly = metricRow(doc, ly, "Net (- Fees)", `${formatNum(m.total_pnl - m.total_fees, 2, true)} USDT`, pnlColorRgb(m.total_pnl - m.total_fees));
  ly = metricRow(doc, ly, "Bakiye", `${formatNum(m.current_balance, 2)} USDT`, BLUE);
  ly = metricRow(doc, ly, "Baslangic Bakiye", `${formatNum(m.initial_balance, 2)} USDT`);
  ly = metricRow(doc, ly, "Profit Factor", formatNum(m.profit_factor, 2), m.profit_factor >= 1 ? GREEN : RED);
  ly = metricRow(doc, ly, "Sharpe Ratio", formatNum(m.sharpe_ratio, 3), m.sharpe_ratio >= 0 ? GREEN : RED);
  ly = metricRow(doc, ly, "Sortino Ratio", formatNum(m.sortino_ratio, 3), m.sortino_ratio >= 0 ? GREEN : RED);
  ly = metricRow(doc, ly, "Calmar Ratio", formatNum(m.calmar_ratio, 3), m.calmar_ratio >= 0 ? GREEN : RED);
  ly = metricRow(doc, ly, "Recovery Factor", formatNum(m.recovery_factor, 2), m.recovery_factor >= 1 ? GREEN : RED);

  // Right column
  let ry = y + 2;
  // Move to right column
  const origMetricRow = (label: string, value: string, color?: readonly [number, number, number]) => {
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8.5);
    doc.setTextColor(...GRAY);
    doc.text(label, col2x, ry);
    doc.setFont("helvetica", "bold");
    doc.setTextColor(...(color || WHITE));
    doc.text(value, col2x + 76, ry, { align: "right" });
    ry += 5;
  };

  origMetricRow("Toplam Islem", `${m.total_trades}`);
  origMetricRow("Kazanan / Kaybeden", `${m.winning_trades} / ${m.losing_trades}`);
  origMetricRow("Win Rate", `${formatNum(m.win_rate, 1)}%`, m.win_rate >= 50 ? GREEN : RED);
  origMetricRow("Avg Win / Loss", `${formatNum(m.avg_win, 2)} / ${formatNum(m.avg_loss, 2)}`);
  origMetricRow("Expectancy", `${formatNum(m.expectancy, 2)} USDT`, pnlColorRgb(m.expectancy));
  origMetricRow("Max Drawdown", `${formatNum(m.max_drawdown_pct, 2)}% (${formatNum(m.max_drawdown_usdt, 2)} USDT)`, RED);
  origMetricRow("Max Run-up", `${formatNum(m.max_runup_pct, 2)}%`, GREEN);
  origMetricRow("Seri Kazanma / Kaybetme", `${m.max_consecutive_wins} / ${m.max_consecutive_losses}`);
  origMetricRow("Ort. Islem Suresi", `${m.avg_duration_min} dk`);
  origMetricRow("Toplam Fee", `${formatNum(m.total_fees, 2)} USDT`, GRAY);

  y = Math.max(ly, ry) + 4;

  // ── En Iyi / En Kotu Islem ──
  doc.setFillColor(16, 185, 129, 0.1);
  doc.roundedRect(col1x - 2, y, 80, 10, 1, 1, "F");
  doc.setFont("helvetica", "bold");
  doc.setFontSize(8);
  doc.setTextColor(...GREEN);
  doc.text(`En Iyi Islem: ${formatNum(m.best_trade_pnl, 2, true)} USDT`, col1x, y + 6.5);

  doc.setFillColor(239, 68, 68, 0.1);
  doc.roundedRect(col2x - 2, y, 80, 10, 1, 1, "F");
  doc.setTextColor(...RED);
  doc.text(`En Kotu Islem: ${formatNum(m.worst_trade_pnl, 2, true)} USDT`, col2x, y + 6.5);

  y += 16;

  // ── Gross Profit/Loss Bar ──
  const totalAbs = m.gross_profit + m.gross_loss;
  if (totalAbs > 0) {
    y = sectionHeader(doc, "KAZANC / KAYIP DAGILIMI", y);
    const barW = pageW - 28;
    const gpWidth = (m.gross_profit / totalAbs) * barW;

    // Profit bar
    doc.setFillColor(...GREEN);
    doc.roundedRect(14, y, gpWidth, 6, 1, 1, "F");
    // Loss bar
    doc.setFillColor(...RED);
    doc.roundedRect(14 + gpWidth, y, barW - gpWidth, 6, 1, 1, "F");

    y += 9;
    doc.setFont("helvetica", "normal");
    doc.setFontSize(7.5);
    doc.setTextColor(...GREEN);
    doc.text(`Gross Profit: ${formatNum(m.gross_profit, 2)} USDT (${formatNum(m.gross_profit / totalAbs * 100, 1)}%)`, 14, y);
    doc.setTextColor(...RED);
    doc.text(`Gross Loss: ${formatNum(m.gross_loss, 2)} USDT (${formatNum(m.gross_loss / totalAbs * 100, 1)}%)`, pageW - 14, y, { align: "right" });
    y += 8;
  }

  // ── Parite Bazinda Sonuclar ──
  if (result.per_symbol.length > 0) {
    y = checkPage(doc, y, 30);
    y = sectionHeader(doc, "PARITE BAZINDA SONUCLAR", y);

    autoTable(doc, {
      startY: y,
      margin: { left: 14, right: 14 },
      head: [["Symbol", "Islem", "Win", "Loss", "Win Rate", "PnL (USDT)", "Net PnL", "PF"]],
      body: result.per_symbol.map(ps => [
        ps.symbol,
        ps.total_trades,
        ps.winning_trades,
        ps.losing_trades,
        `${formatNum(ps.win_rate, 1)}%`,
        formatNum(ps.total_pnl, 2, true),
        formatNum(ps.total_pnl - ps.total_fees, 2, true),
        formatNum(ps.profit_factor, 2),
      ]),
      theme: "plain",
      styles: {
        fontSize: 7.5,
        textColor: [...WHITE],
        cellPadding: 1.5,
        lineWidth: 0,
      },
      headStyles: {
        fillColor: [...DARK_BG],
        textColor: [...BLUE],
        fontStyle: "bold",
        fontSize: 7,
      },
      alternateRowStyles: {
        fillColor: [15, 23, 32],
      },
      columnStyles: {
        0: { fontStyle: "bold" },
        4: { halign: "right" },
        5: { halign: "right" },
        6: { halign: "right" },
        7: { halign: "right" },
      },
      didParseCell: (data) => {
        if (data.section === "body") {
          // Color PnL columns
          if (data.column.index === 5 || data.column.index === 6) {
            const val = parseFloat(String(data.cell.raw).replace("+", ""));
            if (val > 0) data.cell.styles.textColor = [...GREEN];
            else if (val < 0) data.cell.styles.textColor = [...RED];
          }
          // Color Win Rate
          if (data.column.index === 4) {
            const val = parseFloat(String(data.cell.raw));
            data.cell.styles.textColor = val >= 50 ? [...GREEN] : [...RED];
          }
        }
      },
    });

    y = (doc as any).lastAutoTable.finalY + 8;
  }

  // ── Long vs Short ──
  y = checkPage(doc, y, 30);
  y = sectionHeader(doc, "LONG vs SHORT ANALIZI", y);

  const longs = result.trades.filter(t => t.side === "LONG");
  const shorts = result.trades.filter(t => t.side === "SHORT");

  const calcSide = (arr: BacktestTrade[]) => {
    const wins = arr.filter(t => t.pnl_usdt > 0).length;
    const pnl = arr.reduce((s, t) => s + t.pnl_usdt, 0);
    return { count: arr.length, wins, wr: arr.length ? (wins / arr.length * 100) : 0, pnl };
  };
  const ld = calcSide(longs);
  const sd = calcSide(shorts);

  autoTable(doc, {
    startY: y,
    margin: { left: 14, right: 14 },
    head: [["Yon", "Islem", "Kazanan", "Win Rate", "Toplam PnL"]],
    body: [
      ["LONG", ld.count, ld.wins, `${formatNum(ld.wr, 1)}%`, `${formatNum(ld.pnl, 2, true)} USDT`],
      ["SHORT", sd.count, sd.wins, `${formatNum(sd.wr, 1)}%`, `${formatNum(sd.pnl, 2, true)} USDT`],
    ],
    theme: "plain",
    styles: { fontSize: 8, textColor: [...WHITE], cellPadding: 2, lineWidth: 0 },
    headStyles: { fillColor: [...DARK_BG], textColor: [...BLUE], fontStyle: "bold", fontSize: 7 },
    columnStyles: { 3: { halign: "right" }, 4: { halign: "right" } },
    didParseCell: (data) => {
      if (data.section === "body" && data.column.index === 0) {
        data.cell.styles.textColor = data.cell.raw === "LONG" ? [...GREEN] : [...RED];
        data.cell.styles.fontStyle = "bold";
      }
      if (data.section === "body" && data.column.index === 4) {
        const val = parseFloat(String(data.cell.raw));
        data.cell.styles.textColor = val >= 0 ? [...GREEN] : [...RED];
      }
    },
  });
  y = (doc as any).lastAutoTable.finalY + 8;

  // ── Exit Reason Summary ──
  y = checkPage(doc, y, 30);
  y = sectionHeader(doc, "CIKIS NEDENI ANALIZI", y);

  const reasons = [...new Set(result.trades.map(t => t.exit_reason))].sort();
  const reasonData = reasons.map(reason => {
    const trades = result.trades.filter(t => t.exit_reason === reason);
    const pnl = trades.reduce((s, t) => s + t.pnl_usdt, 0);
    const wr = trades.filter(t => t.pnl_usdt > 0).length / trades.length * 100;
    return [reason, trades.length, `${formatNum(wr, 1)}%`, `${formatNum(pnl, 2, true)} USDT`];
  });

  autoTable(doc, {
    startY: y,
    margin: { left: 14, right: 14 },
    head: [["Neden", "Islem", "Win Rate", "Toplam PnL"]],
    body: reasonData,
    theme: "plain",
    styles: { fontSize: 8, textColor: [...WHITE], cellPadding: 2, lineWidth: 0 },
    headStyles: { fillColor: [...DARK_BG], textColor: [...BLUE], fontStyle: "bold", fontSize: 7 },
    columnStyles: { 2: { halign: "right" }, 3: { halign: "right" } },
    didParseCell: (data) => {
      if (data.section === "body" && data.column.index === 3) {
        const val = parseFloat(String(data.cell.raw));
        data.cell.styles.textColor = val >= 0 ? [...GREEN] : [...RED];
      }
    },
  });
  y = (doc as any).lastAutoTable.finalY + 8;

  // ── Trade List (new page) ──
  doc.addPage();
  drawPageFooter(doc, 15);

  // Header band on trade list page
  doc.setFillColor(...NAVY);
  doc.rect(0, 0, pageW, 18, "F");
  drawLogo(doc, 12, 3, 0.5);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(11);
  doc.setTextColor(...WHITE);
  doc.text("Islem Detaylari", pageW - 14, 12, { align: "right" });

  y = 24;

  autoTable(doc, {
    startY: y,
    margin: { left: 8, right: 8 },
    head: [["#", "Symbol", "Side", "Giris", "Entry", "Cikis", "Exit", "Neden", "PnL", "PnL %", "Fee"]],
    body: result.trades.map(t => [
      t.id,
      t.symbol,
      t.side,
      fmtDateTime(t.entry_time),
      formatNum(t.entry_price, 4),
      fmtDateTime(t.exit_time),
      formatNum(t.exit_price, 4),
      t.exit_reason,
      `${formatNum(t.pnl_usdt, 2, true)}`,
      `${formatNum(t.pnl_pct, 2, true)}%`,
      formatNum(t.fee_usdt, 2),
    ]),
    theme: "plain",
    styles: {
      fontSize: 6.5,
      textColor: [...WHITE],
      cellPadding: 1.2,
      lineWidth: 0,
      overflow: "ellipsize",
    },
    headStyles: {
      fillColor: [...DARK_BG],
      textColor: [...BLUE],
      fontStyle: "bold",
      fontSize: 6,
    },
    alternateRowStyles: {
      fillColor: [15, 23, 32],
    },
    columnStyles: {
      0: { cellWidth: 8 },
      1: { fontStyle: "bold", cellWidth: 22 },
      2: { cellWidth: 12, halign: "center" },
      3: { cellWidth: 28 },
      4: { halign: "right", cellWidth: 18 },
      5: { cellWidth: 28 },
      6: { halign: "right", cellWidth: 18 },
      7: { halign: "center", cellWidth: 14 },
      8: { halign: "right", cellWidth: 16 },
      9: { halign: "right", cellWidth: 14 },
      10: { halign: "right", cellWidth: 12 },
    },
    didParseCell: (data) => {
      if (data.section === "body") {
        // Side color
        if (data.column.index === 2) {
          data.cell.styles.textColor = data.cell.raw === "LONG" ? [...GREEN] : [...RED];
          data.cell.styles.fontStyle = "bold";
        }
        // PnL color
        if (data.column.index === 8 || data.column.index === 9) {
          const val = parseFloat(String(data.cell.raw).replace("+", "").replace("%", ""));
          data.cell.styles.textColor = val >= 0 ? [...GREEN] : [...RED];
        }
        // Exit reason color
        if (data.column.index === 7) {
          const r = String(data.cell.raw);
          if (r.startsWith("TP")) data.cell.styles.textColor = [...GREEN];
          else if (r === "SL") data.cell.styles.textColor = [...RED];
          else data.cell.styles.textColor = [234, 179, 8]; // yellow
        }
      }
    },
    didDrawPage: () => {
      // Mini header on continuation pages
      const pg = doc.getNumberOfPages();
      if (pg > 2) {
        doc.setFillColor(...NAVY);
        doc.rect(0, 0, pageW, 10, "F");
        doc.setFont("helvetica", "bold");
        doc.setFontSize(7);
        doc.setTextColor(...GRAY);
        doc.text("Sigma Kapital — Backtest Islem Detaylari (devam)", 10, 7);
      }
      drawPageFooter(doc, 15);
    },
  });

  // ── Add page footers to first pages ──
  const totalPages = doc.getNumberOfPages();
  for (let i = 1; i <= totalPages; i++) {
    doc.setPage(i);
    const pageH = doc.internal.pageSize.getHeight();
    doc.setDrawColor(...BLUE);
    doc.setLineWidth(0.3);
    doc.line(10, pageH - 12, pageW - 10, pageH - 12);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(7);
    doc.setTextColor(...GRAY);
    doc.text("Sigma Kapital Trading Technologies & Market Making Services", 10, pageH - 8);
    doc.text(`Sayfa ${i} / ${totalPages}`, pageW - 10, pageH - 8, { align: "right" });
    doc.text("GIZLI — Sadece dahili kullanim icin", pageW / 2, pageH - 8, { align: "center" });
  }

  // Save
  const timestamp = new Date().toISOString().slice(0, 10);
  doc.save(`SigmaKapital_Backtest_${timestamp}.pdf`);
}
