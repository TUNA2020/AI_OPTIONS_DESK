import React, { useMemo } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from "recharts";

function formatNum(value, digits = 2) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n.toFixed(digits) : "0.00";
}

function formatNumWithSign(value, digits = 2) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "0.00";
  const formatted = n.toFixed(digits);
  return n > 0 ? `+${formatted}` : n < 0 ? `${formatted}` : formatted;
}

function pnlColorClass(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "";
  return n > 0 ? "pnl-positive" : n < 0 ? "pnl-negative" : "";
}

function copyToClipboard(value, event) {
  if (value === null || value === undefined) return;
  const text = typeof value === 'number' ? value.toLocaleString() : String(value);
  navigator.clipboard.writeText(text).then(() => {
    const el = event.currentTarget;
    const originalTitle = el.title || '';
    el.title = 'Copied to clipboard!';
    setTimeout(() => { el.title = originalTitle; }, 1500);
  }).catch(err => console.error('Copy failed', err));
}

export default React.memo(function DayWisePnlPage({ pnl }) {
  // Get all days and sort by date ascending (oldest to newest) for chart
  const sortedDayWise = [...(pnl?.day_wise || [])].sort((a, b) => {
    const dateA = new Date(a.day);
    const dateB = new Date(b.day);
    return dateA - dateB;
  });

  // Filter out non-trading days (zero pnl and zero unrealized), but keep first if all zeros
  const activeDayWise = sortedDayWise.filter((row, idx, arr) => {
    const hasActivity = row.pnl !== 0 || row.unrealized !== 0;
    // Keep the first entry even if zero to show start date
    if (idx === 0) return true;
    return hasActivity;
  });

  // Compute cumulative from the sorted active days, filtering zero net P&L
  const cumulativeData = React.useMemo(() => {
    const data = [];
    let runningTotal = 0;
    // First, filter out days with zero net P&L (but keep first entry if all are zero)
    const nonZeroDays = activeDayWise.filter((row, idx) => {
      const net = row.pnl + (row.unrealized || 0);
      // Always keep the first entry to show the starting point
      if (idx === 0) return true;
      return net !== 0;
    });

    nonZeroDays.forEach((row) => {
      const net = row.pnl + (row.unrealized || 0);
      runningTotal += net;
      data.push({
        day: row.day,
        dateObj: new Date(row.day),
        dayLabel: new Date(row.day).toLocaleDateString('en-IN', {
          day: '2-digit',
          month: 'short'
        }),
        cumulative: runningTotal,
        daily: net
      });
    });
    return data;
  }, [activeDayWise]);

  // For tables: sort descending (recent first) and filter zero net pnl
  const tableData = [...activeDayWise].sort((a, b) => new Date(b.day) - new Date(a.day));

  const hasData = activeDayWise.length > 0;

  return (
    <div className="page" style={{ animation: "entryFade 0.6s ease-out forwards" }}>
      <div className="top">
        <div className="heroCopyBlock">
          <div className="eyebrow">Performance Analytics</div>
          <h1>Day-wise P&L</h1>
          <p className="heroCopy">
            Historical daily profit/loss breakdown across trading sessions.
          </p>
        </div>
      </div>

      <div className="gradient-divider"></div>

      <section className="grid">
        {/* Cumulative PnL Chart */}
        <article className="panel full tilt-card entry-animate">
          <div className="section-badge">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 3v18h18" />
              <path d="M18.7 8l-5.1 5.2-2.8-2.7L7 14.3" />
            </svg>
            Cumulative P&L
          </div>
          <h2>Total P&L from First Trading Day</h2>
          <div className="chartWrap">
            {hasData ? (
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={cumulativeData} margin={{ top: 10, right: 30, left: 60, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.1)" vertical={false} />
                  <XAxis
                    dataKey="dayLabel"
                    stroke="#aab7cb"
                    tick={{ fill: '#aab7cb', fontSize: 12 }}
                    axisLine={{ stroke: 'rgba(148,163,184,0.2)' }}
                    tickLine={{ stroke: 'rgba(148,163,184,0.2)' }}
                  />
                  <YAxis
                    width={50}
                    stroke="#aab7cb"
                    tick={{ fill: '#aab7cb', fontSize: 12 }}
                    axisLine={{ stroke: 'rgba(148,163,184,0.2)' }}
                    tickLine={{ stroke: 'rgba(148,163,184,0.2)' }}
                    tickFormatter={(value) => formatNum(value, 0)}
                    domain={['auto', 'auto']}
                    allowDecimals={false}
                  />
                  <Tooltip
                    cursor={{ stroke: 'rgba(20,184,166,0.5)', strokeWidth: 1 }}
                    formatter={(value, name) => {
                      const numValue = Number(value);
                      return [formatNumWithSign(numValue), name === 'cumulative' ? 'Total P&L' : 'Daily'];
                    }}
                    labelFormatter={(label, payload) => {
                      if (payload && payload.length > 0) {
                        return payload[0].payload.day || label;
                      }
                      return label;
                    }}
                    contentStyle={{
                      background: 'rgba(16, 26, 46, 0.95)',
                      border: '1px solid rgba(20, 184, 166, 0.4)',
                      borderRadius: '8px',
                      boxShadow: '0 6px 20px rgba(2, 8, 23, 0.4)',
                      padding: '8px 12px',
                      minWidth: '120px'
                    }}
                    itemStyle={{
                      color: '#aab7cb',
                      fontFamily: 'Space Grotesk, sans-serif',
                      fontSize: '0.85rem',
                      margin: '2px 0'
                    }}
                    labelStyle={{
                      color: '#aab7cb',
                      fontFamily: 'Space Grotesk, sans-serif',
                      fontSize: '0.85rem',
                      fontWeight: 600,
                      marginBottom: '4px',
                      borderBottom: '1px solid rgba(148, 163, 184, 0.1)',
                      paddingBottom: '4px'
                    }}
                  />
                  <Line
                    type="monotone"
                    dataKey="cumulative"
                    stroke="#14b8a6"
                    strokeWidth={2.5}
                    dot={{ r: 4 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="empty-state">
                <div className="empty-icon">
                  <svg viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <line x1="8" y1="40" x2="24" y2="40"></line>
                    <line x1="8" y1="28" x2="56" y2="28"></line>
                    <line x1="8" y1="16" x2="40" y2="16"></line>
                    <path d="M40 16h16"></path>
                    <path d="M40 28h8"></path>
                  </svg>
                </div>
                <p>No trading data</p>
                <small>PnL data will appear here after trading sessions</small>
              </div>
            )}
          </div>
        </article>

        {/* Day-wise PnL Table */}
        <article className="panel full tilt-card entry-animate">
          <div className="section-badge">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="20" x2="18" y2="10"></line>
              <line x1="12" y1="20" x2="12" y2="4"></line>
              <line x1="6" y1="20" x2="6" y2="14"></line>
            </svg>
            Daily Performance
          </div>
          <h2>Day-wise PnL History</h2>
          <div className="tableWrap">
            <table>
              <thead>
                <tr>
                  <th title="Trading day date">Day</th>
                  <th title="Profit or Loss realized on that day">Realized PnL</th>
                  <th title="Unrealized open positions at day end">Unrealized</th>
                  <th title="Net PnL including open positions">Net PnL</th>
                </tr>
              </thead>
              <tbody>
                {tableData.length > 0 ? (
                  tableData.map((row, idx) => {
                    const netPnl = row.pnl + (row.unrealized || 0);
                    // Skip rows with net PnL = 0 unless it's the only row
                    if (netPnl === 0 && tableData.length > 1) return null;
                    return (
                      <tr key={row.day}>
                        <td>{row.day}</td>
                        <td className={pnlColorClass(row.pnl)} onClick={(e) => copyToClipboard(row.pnl, e)} title="Click to copy">
                          {formatNumWithSign(row.pnl)}
                        </td>
                        <td className={pnlColorClass(row.unrealized || 0)} onClick={(e) => copyToClipboard(row.unrealized || 0, e)} title="Click to copy">
                          {formatNumWithSign(row.unrealized || 0)}
                        </td>
                        <td className={pnlColorClass(netPnl)} onClick={(e) => copyToClipboard(netPnl, e)} title="Click to copy" style={{ fontWeight: 600 }}>
                          {formatNumWithSign(netPnl)}
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan="4" className="empty-state">
                      <div className="empty-icon">
                        <svg viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="1.5">
                          <line x1="8" y1="40" x2="24" y2="40"></line>
                          <line x1="8" y1="28" x2="56" y2="28"></line>
                          <line x1="8" y1="16" x2="40" y2="16"></line>
                          <path d="M40 16h16"></path>
                          <path d="M40 28h8"></path>
                        </svg>
                      </div>
                      <p>No daily P&L data</p>
                      <small>Daily performance will appear here after trading sessions</small>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </article>

        {/* Cumulative Table */}
        <article className="panel full tilt-card entry-animate">
          <div className="section-badge">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10"></circle>
              <path d="M12 6v6l4 2"></path>
            </svg>
            Cumulative Performance
          </div>
          <h2>Cumulative P&L Curve</h2>
          <div className="tableWrap">
            <table>
              <thead>
                <tr>
                  <th title="Trading day">Day</th>
                  <th title="Cumulative P&L up to this day">Cumulative PnL</th>
                  <th title="Daily change">Daily Δ</th>
                </tr>
              </thead>
              <tbody>
                {cumulativeData.length > 0 ? (
                  cumulativeData.slice().reverse().map((row, idx) => {
                    const originalIndex = cumulativeData.length - 1 - idx;
                    const prevCum = originalIndex > 0 ? cumulativeData[originalIndex - 1].cumulative : 0;
                    const dailyChange = row.cumulative - prevCum;
                    // Skip zero daily change rows unless it's the only row
                    if (dailyChange === 0 && cumulativeData.length > 1) return null;
                    return (
                      <tr key={row.day}>
                        <td>{row.day}</td>
                        <td className={pnlColorClass(row.cumulative)} onClick={(e) => copyToClipboard(row.cumulative, e)} title="Click to copy" style={{ fontWeight: 600 }}>
                          {formatNum(row.cumulative)}
                        </td>
                        <td className={pnlColorClass(dailyChange)} onClick={(e) => copyToClipboard(dailyChange, e)} title="Click to copy">
                          {dailyChange !== 0 ? (dailyChange > 0 ? "+" : "") + formatNum(dailyChange) : "-"}
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan="3" className="empty-state">
                      <div className="empty-icon">
                        <svg viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="1.5">
                          <path d="M16 44V24l12-8 12 8v20"></path>
                          <path d="M28 44v12"></path>
                          <path d="M12 20h40"></path>
                          <circle cx="32" cy="12" r="4"></circle>
                        </svg>
                      </div>
                      <p>No cumulative data</p>
                      <small>Cumulative performance will appear here</small>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </article>
      </section>
    </div>
  );
});
