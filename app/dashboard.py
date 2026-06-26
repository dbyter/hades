"""Single-page dashboard — polished light theme (Tailwind + Alpine). Tabs: Stocks | Options.

Sortable grids, company names, KPI cards, inline data-bars. Data loads via /api/* fetch."""


def render_dashboard() -> str:
    return _HTML


_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Momentum Trader</title>
<script src="https://cdn.tailwindcss.com"></script>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  [x-cloak]{display:none!important}
  *{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif}
  .num{font-variant-numeric:tabular-nums;font-feature-settings:"tnum";letter-spacing:-.01em}
  body{background:#f6f7fb}
  .app-bg{position:fixed;inset:0;z-index:-1;background:
    radial-gradient(900px 400px at 12% -8%, rgba(99,102,241,.10), transparent 60%),
    radial-gradient(800px 380px at 100% 0%, rgba(139,92,246,.10), transparent 55%);}
  .card{transition:box-shadow .2s ease, transform .2s ease}
  .kpi:hover{box-shadow:0 10px 30px -12px rgba(30,41,59,.25);transform:translateY(-2px)}
  thead th{position:sticky;top:0;background:rgba(248,250,252,.92);backdrop-filter:blur(6px);z-index:5}
  tbody tr{transition:background-color .12s ease}
  ::-webkit-scrollbar{height:9px;width:9px}
  ::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:9999px}
  ::-webkit-scrollbar-thumb:hover{background:#94a3b8}
  .grad{background:linear-gradient(135deg,#6366f1,#8b5cf6)}
  .grad:hover{background:linear-gradient(135deg,#4f46e5,#7c3aed)}
  th.s{cursor:pointer;user-select:none}
  th.s:hover{color:#475569}
</style>
</head>
<body class="text-slate-800 min-h-screen">
<div class="app-bg"></div>
<div class="max-w-[1680px] mx-auto px-6 py-7" x-data="dash()" x-init="init()" x-cloak>

  <!-- Header -->
  <header class="flex items-center justify-between mb-7 flex-wrap gap-4">
    <div class="flex items-center gap-3">
      <div class="w-11 h-11 rounded-2xl grad shadow-lg shadow-indigo-500/30 flex items-center justify-center">
        <svg class="w-6 h-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 17l6-6 4 4 8-8M21 7v5m0-5h-5"/></svg>
      </div>
      <div>
        <h1 class="text-xl font-extrabold text-slate-900 tracking-tight">Momentum Trader</h1>
        <p class="text-slate-500 text-[12.5px]">Run after the close · exits & rotation follow the backtested rules</p>
      </div>
    </div>
    <div class="flex items-center gap-3">
      <span class="text-slate-400 text-xs num hidden sm:block" x-text="asOf ? ('as of ' + asOf) : ''"></span>
      <div class="inline-flex rounded-xl bg-white border border-slate-200 p-1 shadow-sm">
        <button @click="activeTab='portfolio'" :class="tabCls('portfolio')">Portfolio</button>
        <button @click="activeTab='stocks'" :class="tabCls('stocks')">Stocks</button>
        <button @click="activeTab='longcalls'" :class="tabCls('longcalls')">Long Calls</button>
        <button @click="activeTab='options'" :class="tabCls('options')">Spreads</button>
      </div>
      <button @click="refreshAll()" :disabled="stockStatus==='computing'||candStatus==='computing'"
        class="grad rounded-xl text-white px-4 py-2 text-[13px] font-semibold shadow-md shadow-indigo-500/25 disabled:opacity-50 flex items-center gap-2">
        <svg class="w-4 h-4" :class="(stockStatus==='computing'||candStatus==='computing')?'animate-spin':''" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h5M20 20v-5h-5M5 9a7 7 0 0112-3m2 9a7 7 0 01-12 3"/></svg>
        <span x-text="(stockStatus==='computing'||candStatus==='computing') ? 'Refreshing…' : 'Refresh data'"></span>
      </button>
    </div>
  </header>

  <!-- ═══════════ PORTFOLIO ═══════════ -->
  <div x-show="activeTab==='portfolio'" x-transition.opacity.duration.200ms class="space-y-6">
    <template x-if="!portfolio"><div class="card bg-white rounded-2xl border border-slate-200/80 px-6 py-12 text-center text-slate-400 shadow-sm">Loading portfolio…</div></template>
    <template x-if="portfolio">
      <div class="space-y-6">

        <!-- Hero KPIs -->
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <div class="kpi card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
            <div class="text-slate-500 text-xs font-medium uppercase tracking-wide">Total value</div>
            <div class="text-3xl font-bold text-slate-900 num mt-2" x-text="'$'+fmtMoney(portfolio.total)"></div>
            <div class="text-xs num mt-0.5 font-medium" :class="retCls(portfolio.day_change)" x-text="portfolio.day_change!=null ? ((portfolio.day_change>=0?'▲ +$':'▼ -$')+fmtMoney(Math.abs(portfolio.day_change))+' today') : 'today —'"></div>
          </div>
          <div class="kpi card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
            <div class="text-slate-500 text-xs font-medium uppercase tracking-wide">Unrealized P&L</div>
            <div class="text-3xl font-bold num mt-2" :class="retCls(portfolio.pnl)" x-text="(portfolio.pnl>=0?'+$':'-$')+fmtMoney(Math.abs(portfolio.pnl))"></div>
            <div class="text-xs num mt-0.5 font-medium" :class="retCls(portfolio.pnl_pct)" x-text="(portfolio.pnl_pct>=0?'+':'')+portfolio.pnl_pct?.toFixed(1)+'%'"></div>
          </div>
          <div class="kpi card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
            <div class="text-slate-500 text-xs font-medium uppercase tracking-wide">YTD</div>
            <div class="text-3xl font-bold num mt-2" :class="retCls(portfolio.periods.ytd)" x-text="pct(portfolio.periods.ytd)"></div>
            <div class="text-[11px] text-slate-400 mt-0.5">holdings-weighted</div>
          </div>
          <div class="kpi card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
            <div class="text-slate-500 text-xs font-medium uppercase tracking-wide">Positions</div>
            <div class="text-3xl font-bold text-slate-900 num mt-2" x-text="portfolio.positions_count"></div>
          </div>
        </div>

        <!-- Period returns + equity curve -->
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div class="card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
            <div class="text-sm font-semibold text-slate-700 mb-3">Period return <span class="text-slate-400 font-normal text-xs">· current holdings</span></div>
            <div class="space-y-2.5">
              <template x-for="w in [['Week','wtd'],['Month','mtd'],['Year','ytd']]" :key="w[1]">
                <div class="flex items-center justify-between">
                  <span class="text-slate-500 text-[13px]" x-text="w[0]"></span>
                  <span class="num font-semibold text-[15px]" :class="retCls(portfolio.periods[w[1]])" x-text="pct(portfolio.periods[w[1]])"></span>
                </div>
              </template>
            </div>
            <p class="text-[10px] text-slate-400 mt-3 leading-tight">Price return of current holdings (excludes options). Assumes shares held all period.</p>
          </div>
          <div class="card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5 lg:col-span-2">
            <div class="text-sm font-semibold text-slate-700 mb-3">Portfolio value</div>
            <template x-if="portfolio.nav_history.length < 2">
              <div class="h-28 flex items-center justify-center text-slate-400 text-[13px]">📈 The equity curve builds daily — check back tomorrow.</div>
            </template>
            <template x-if="portfolio.nav_history.length >= 2">
              <div x-html="equityChart(portfolio.nav_history)"></div>
            </template>
          </div>
        </div>

        <!-- Allocation -->
        <div class="card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
          <div class="text-sm font-semibold text-slate-700 mb-3">Allocation</div>
          <div class="flex h-3 w-full rounded-full overflow-hidden bg-slate-100">
            <template x-for="b in portfolio.buckets" :key="b.name"><div :style="`width:${b.weight}%;background:${bucketColor(b.name)}`" :title="`${bucketLabel(b.name)} ${b.weight}%`"></div></template>
          </div>
          <div class="flex flex-wrap gap-x-6 gap-y-2 mt-3">
            <template x-for="b in portfolio.buckets" :key="b.name">
              <div class="flex items-center gap-2 text-[13px]">
                <span class="w-2.5 h-2.5 rounded-sm" :style="`background:${bucketColor(b.name)}`"></span>
                <span class="font-medium text-slate-700" x-text="bucketLabel(b.name)"></span>
                <span class="text-slate-500 num" x-text="'$'+fmtMoney(b.value)+' · '+b.weight+'%'"></span>
              </div>
            </template>
          </div>
        </div>

        <!-- By bucket -->
        <template x-for="b in portfolio.buckets" :key="b.name">
          <div>
            <div class="flex items-center justify-between mb-2.5 px-1">
              <h2 class="text-sm font-semibold text-slate-700 flex items-center gap-2"><span class="w-2.5 h-2.5 rounded-sm" :style="`background:${bucketColor(b.name)}`"></span><span x-text="bucketLabel(b.name)"></span><span class="text-slate-400 font-normal" x-text="'· $'+fmtMoney(b.value)+' · '+b.weight+'% of book'"></span></h2>
              <div class="flex items-center gap-3 text-[12px] num">
                <span class="text-slate-400">YTD <span :class="retCls(b.ytd)" x-text="pct(b.ytd)"></span></span>
                <span class="font-semibold" :class="retCls(b.pnl)" x-text="(b.pnl>=0?'+$':'-$')+fmtMoney(Math.abs(b.pnl))"></span>
              </div>
            </div>
            <div class="card bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden overflow-x-auto">
              <table class="w-full border-collapse text-[13px]">
                <thead><tr class="text-[11px] uppercase tracking-wide text-slate-400 border-b border-slate-200">
                  <th class="px-4 py-2.5 text-left font-semibold">Ticker</th>
                  <th class="px-4 py-2.5 text-right font-semibold">Value</th>
                  <th class="px-4 py-2.5 text-right font-semibold">% Book</th>
                  <th class="px-4 py-2.5 text-right font-semibold">1w</th>
                  <th class="px-4 py-2.5 text-right font-semibold">1m</th>
                  <th class="px-4 py-2.5 text-right font-semibold">YTD</th>
                  <th class="px-4 py-2.5 text-right font-semibold">P&L</th>
                </tr></thead>
                <tbody>
                  <template x-for="p in b.positions" :key="p.id">
                    <tr class="border-b border-slate-100 last:border-0 hover:bg-indigo-50/40">
                      <td class="px-4 py-2.5 text-left"><div class="font-bold text-slate-900" x-text="p.ticker"></div><div class="text-[11px] text-slate-400 truncate max-w-[150px]" x-text="nameFor(p.ticker)"></div></td>
                      <td class="px-4 py-2.5 text-right text-slate-700 num" x-text="'$'+fmtMoney(p.mv)"></td>
                      <td class="px-4 py-2.5 text-right text-slate-500 num" x-text="portfolio.total?((p.mv/portfolio.total*100).toFixed(1)+'%'):'—'"></td>
                      <td class="px-4 py-2.5 text-right num" :class="retCls(p.ret.wtd)" x-text="pct(p.ret.wtd)"></td>
                      <td class="px-4 py-2.5 text-right num" :class="retCls(p.ret.mtd)" x-text="pct(p.ret.mtd)"></td>
                      <td class="px-4 py-2.5 text-right num" :class="retCls(p.ret.ytd)" x-text="pct(p.ret.ytd)"></td>
                      <td class="px-4 py-2.5 text-right num font-semibold" :class="retCls(p.pnl)" x-text="p.pnl!=null?(p.pnl>=0?'+$':'-$')+fmtMoney(Math.abs(p.pnl)):'—'"></td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>
          </div>
        </template>
      </div>
    </template>
  </div>

  <!-- ═══════════ STOCKS ═══════════ -->
  <div x-show="activeTab==='stocks'" x-transition.opacity.duration.200ms class="space-y-6">

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="kpi card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
        <div class="flex items-center justify-between"><span class="text-slate-500 text-xs font-medium uppercase tracking-wide">Holdings</span>
          <span class="w-8 h-8 rounded-xl bg-indigo-50 text-indigo-600 flex items-center justify-center"><svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 7h18M3 12h18M3 17h18"/></svg></span></div>
        <div class="text-3xl font-bold text-slate-900 num mt-2" x-text="stockSummary.count"></div>
      </div>
      <div class="kpi card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
        <div class="flex items-center justify-between"><span class="text-slate-500 text-xs font-medium uppercase tracking-wide">Market value</span>
          <span class="w-8 h-8 rounded-xl bg-sky-50 text-sky-600 flex items-center justify-center"><svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 7h18v10H3zM7 7v10m10-10v10"/></svg></span></div>
        <div class="text-3xl font-bold text-slate-900 num mt-2" x-text="'$'+fmtMoney(stockSummary.val)"></div>
      </div>
      <div class="kpi card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
        <div class="flex items-center justify-between"><span class="text-slate-500 text-xs font-medium uppercase tracking-wide">Open P&L</span>
          <span class="w-8 h-8 rounded-xl flex items-center justify-center" :class="stockSummary.pnl>=0?'bg-emerald-50 text-emerald-600':'bg-rose-50 text-rose-600'"><svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" :d="stockSummary.pnl>=0?'M3 17l6-6 4 4 8-8':'M3 7l6 6 4-4 8 8'"/></svg></span></div>
        <div class="text-3xl font-bold num mt-2" :class="retCls(stockSummary.pnl)" x-text="(stockSummary.pnl>=0?'+$':'-$')+fmtMoney(Math.abs(stockSummary.pnl))"></div>
        <div class="text-xs num mt-0.5 font-medium" :class="retCls(stockSummary.pnlPct)" x-text="(stockSummary.pnlPct>=0?'▲ ':'▼ ')+Math.abs(stockSummary.pnlPct).toFixed(1)+'%'"></div>
      </div>
      <div class="kpi card bg-white rounded-2xl border shadow-sm p-5" :class="stockSummary.rotate?'border-amber-300 ring-1 ring-amber-200':'border-slate-200/80'">
        <div class="flex items-center justify-between"><span class="text-slate-500 text-xs font-medium uppercase tracking-wide">Rotation alerts</span>
          <span class="w-8 h-8 rounded-xl flex items-center justify-center" :class="stockSummary.rotate?'bg-amber-100 text-amber-600':'bg-slate-100 text-slate-400'"><svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01M10.3 4.3 2.6 18a1.5 1.5 0 001.3 2.2h16.2a1.5 1.5 0 001.3-2.2L13.7 4.3a1.5 1.5 0 00-2.6 0z"/></svg></span></div>
        <div class="text-3xl font-bold num mt-2" :class="stockSummary.rotate?'text-amber-600':'text-slate-900'" x-text="stockSummary.rotate"></div>
      </div>
    </div>

    <template x-if="stockRotation.dropouts.length || stockRotation.new_entrants.length">
      <div class="rounded-2xl border border-amber-300 bg-gradient-to-r from-amber-50 to-orange-50 px-5 py-3.5 text-sm text-amber-800 space-y-1 shadow-sm">
        <div x-show="stockRotation.dropouts.length" class="flex items-start gap-2"><span class="text-amber-500 mt-0.5">⚠</span><div><span class="font-semibold" x-text="stockRotation.dropouts.length"></span> holding(s) dropped out of the top-50 — consider rotating: <span class="font-semibold" x-text="stockRotation.dropouts.join(', ')"></span></div></div>
        <div x-show="stockRotation.new_entrants.length" class="text-amber-700/80 pl-6">New in the top-50 (not held): <span x-text="stockRotation.new_entrants.slice(0,14).join(', ')"></span></div>
      </div>
    </template>

    <div>
      <h2 class="text-sm font-semibold text-slate-700 mb-2.5 px-1">Your positions</h2>
      <div class="card bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden">
        <table class="w-full border-collapse text-[13px]">
          <thead>
            <tr class="text-[11px] uppercase tracking-wide text-slate-400 border-b border-slate-200">
              <th class="s px-4 py-3 text-left font-semibold" @click="toggleSort('spos','ticker')">Ticker<span class="text-indigo-500 ml-0.5" x-text="sortIcon('spos','ticker')"></span></th>
              <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('spos','shares')">Shares<span class="text-indigo-500 ml-0.5" x-text="sortIcon('spos','shares')"></span></th>
              <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('spos','entry_price')">Entry<span class="text-indigo-500 ml-0.5" x-text="sortIcon('spos','entry_price')"></span></th>
              <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('spos','current_price')">Now<span class="text-indigo-500 ml-0.5" x-text="sortIcon('spos','current_price')"></span></th>
              <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('spos','pnl')">P&L $<span class="text-indigo-500 ml-0.5" x-text="sortIcon('spos','pnl')"></span></th>
              <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('spos','pnl_pct')">P&L %<span class="text-indigo-500 ml-0.5" x-text="sortIcon('spos','pnl_pct')"></span></th>
              <th class="s px-4 py-3 text-left font-semibold" @click="toggleSort('spos','action')">Status<span class="text-indigo-500 ml-0.5" x-text="sortIcon('spos','action')"></span></th>
              <th class="px-4 py-3 text-right font-semibold"></th>
            </tr>
          </thead>
          <tbody>
            <template x-if="stockPosLoading"><tr><td colspan="8" class="px-4 py-8 text-center text-slate-400">Pricing…</td></tr></template>
            <template x-if="!stockPosLoading && stockPositions.length===0"><tr><td colspan="8" class="px-4 py-12 text-center text-slate-400">No positions yet — add one from the leaders below. 📈</td></tr></template>
            <template x-for="p in sortedStockPositions" :key="p.id">
              <tr class="border-b border-slate-100 last:border-0 hover:bg-indigo-50/40" :class="p.action==='rotate'?'bg-amber-50/60':''">
                <td class="px-4 py-3 text-left"><div class="font-bold text-slate-900" x-text="p.ticker"></div><div class="text-[11px] text-slate-400 truncate max-w-[150px]" x-text="nameFor(p.ticker)"></div></td>
                <td class="px-4 py-3 text-right text-slate-500 num" x-text="(+p.shares).toFixed(0)"></td>
                <td class="px-4 py-3 text-right text-slate-500 num" x-text="'$'+(+p.entry_price).toFixed(2)"></td>
                <td class="px-4 py-3 text-right text-slate-800 num" x-text="p.current_price!=null?'$'+p.current_price.toFixed(2):'—'"></td>
                <td class="px-4 py-3 text-right num font-semibold" :class="retCls(p.pnl)" x-text="p.pnl!=null?(p.pnl>=0?'+$':'-$')+fmtMoney(Math.abs(p.pnl)):'—'"></td>
                <td class="px-4 py-3 text-right num" :class="retCls(p.pnl_pct)" x-text="p.pnl_pct!=null?(p.pnl_pct>=0?'+':'')+p.pnl_pct.toFixed(1)+'%':'—'"></td>
                <td class="px-4 py-3 text-left"><span class="rounded-full px-2.5 py-0.5 text-[11px] font-semibold" :class="p.action==='rotate'?'bg-amber-100 text-amber-700':'bg-emerald-100 text-emerald-700'" x-text="p.action_label"></span></td>
                <td class="px-4 py-3 text-right">
                  <div class="flex items-center justify-end gap-2">
                    <button @click="tagStock(p, p.tracker==='momentum'?'core':'momentum')" title="Click to switch strategy bucket" class="text-[11px] rounded-full px-2 py-0.5 border font-medium" :class="p.tracker==='momentum'?'border-indigo-200 text-indigo-600 bg-indigo-50':'border-sky-200 text-sky-600 bg-sky-50'" x-text="p.tracker==='momentum'?'Momentum':'Core'"></button>
                    <button @click="askCloseStock(p)" class="rounded-lg border border-slate-300 hover:bg-slate-100 text-slate-700 px-3 py-1 text-[12px] font-medium transition-colors">Sell</button>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>

    <div>
      <h2 class="text-sm font-semibold text-slate-700 mb-2.5 px-1">Momentum leaders <span class="text-slate-400 font-normal">— click any column to sort</span></h2>
      <template x-if="stockStatus==='computing'"><div class="card bg-white rounded-2xl border border-slate-200/80 px-6 py-12 text-center text-slate-400 shadow-sm">Computing momentum + metrics… ~30–60s</div></template>
      <template x-if="stockStatus==='error'"><div class="rounded-2xl border border-rose-200 bg-rose-50 px-6 py-6 text-center text-rose-600">Failed to compute. Check the server log.</div></template>
      <template x-if="stockStatus==='ready'">
        <div class="card bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden overflow-x-auto">
          <table class="w-full border-collapse text-[13px]">
            <thead>
              <tr class="text-[11px] uppercase tracking-wide text-slate-400 border-b border-slate-200">
                <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('stocks','rank')">#<span class="text-indigo-500 ml-0.5" x-text="sortIcon('stocks','rank')"></span></th>
                <th class="s px-4 py-3 text-left font-semibold" @click="toggleSort('stocks','ticker')">Ticker<span class="text-indigo-500 ml-0.5" x-text="sortIcon('stocks','ticker')"></span></th>
                <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('stocks','market_cap')">Mkt Cap<span class="text-indigo-500 ml-0.5" x-text="sortIcon('stocks','market_cap')"></span></th>
                <th class="s px-4 py-3 text-left font-semibold" @click="toggleSort('stocks','weight')">Weight<span class="text-indigo-500 ml-0.5" x-text="sortIcon('stocks','weight')"></span></th>
                <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('stocks','price')">Price<span class="text-indigo-500 ml-0.5" x-text="sortIcon('stocks','price')"></span></th>
                <th class="s px-4 py-3 text-left font-semibold" @click="toggleSort('stocks','rsi')">RSI<span class="text-indigo-500 ml-0.5" x-text="sortIcon('stocks','rsi')"></span></th>
                <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('stocks','from_52w_high')">52w Hi<span class="text-indigo-500 ml-0.5" x-text="sortIcon('stocks','from_52w_high')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('stocks','ret_1w')">1w<span class="text-indigo-500 ml-0.5" x-text="sortIcon('stocks','ret_1w')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('stocks','ret_1m')">1m<span class="text-indigo-500 ml-0.5" x-text="sortIcon('stocks','ret_1m')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('stocks','ret_3m')">3m<span class="text-indigo-500 ml-0.5" x-text="sortIcon('stocks','ret_3m')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('stocks','ytd')">YTD<span class="text-indigo-500 ml-0.5" x-text="sortIcon('stocks','ytd')"></span></th>
                <th class="s px-4 py-3 text-left font-semibold" @click="toggleSort('stocks','rating')">Analysts<span class="text-indigo-500 ml-0.5" x-text="sortIcon('stocks','rating')"></span></th>
                <th class="px-4 py-3 text-right font-semibold"></th>
              </tr>
            </thead>
            <tbody>
              <template x-for="s in sortedStocks" :key="s.ticker">
                <tr class="border-b border-slate-100 last:border-0 hover:bg-indigo-50/40">
                  <td class="px-4 py-2.5 text-right num"><span class="inline-flex items-center justify-center w-6 h-6 rounded-lg text-[11px] font-bold" :class="s.rank<=10?'bg-indigo-100 text-indigo-700':'bg-slate-100 text-slate-500'" x-text="s.rank"></span></td>
                  <td class="px-4 py-2.5 text-left"><div class="font-bold text-slate-900" x-text="s.ticker"></div><div class="text-[11px] text-slate-400 truncate max-w-[150px]" x-text="s.name||''" :title="s.name||''"></div></td>
                  <td class="px-4 py-2.5 text-right text-slate-600 num" x-text="fmtCap(s.market_cap)"></td>
                  <td class="px-4 py-2.5"><div class="flex items-center gap-2"><div class="h-1.5 w-16 bg-slate-100 rounded-full overflow-hidden"><div class="h-full rounded-full grad" :style="`width:${barW(s.weight*100, 20)}%`"></div></div><span class="text-slate-500 num text-xs" x-text="(s.weight*100).toFixed(1)+'%'"></span></div></td>
                  <td class="px-4 py-2.5 text-right text-slate-700 num" x-text="s.price!=null?'$'+s.price.toFixed(2):'—'"></td>
                  <td class="px-4 py-2.5"><div class="flex items-center gap-2"><div class="h-1.5 w-14 bg-slate-100 rounded-full overflow-hidden"><div class="h-full rounded-full" :class="rsiBar(s.rsi)" :style="`width:${s.rsi??0}%`"></div></div><span class="num text-xs font-medium" :class="rsiCls(s.rsi)" x-text="s.rsi ?? '—'"></span></div></td>
                  <td class="px-4 py-2.5 text-right num" :class="s.from_52w_high!=null&&s.from_52w_high<-10?'text-rose-600':'text-emerald-600'" x-text="s.from_52w_high!=null?s.from_52w_high.toFixed(1)+'%':'—'"></td>
                  <td class="px-2 py-2.5" x-html="bar(s.ret_1w,15)"></td>
                  <td class="px-2 py-2.5" x-html="bar(s.ret_1m,30)"></td>
                  <td class="px-2 py-2.5" x-html="bar(s.ret_3m,80)"></td>
                  <td class="px-2 py-2.5" x-html="bar(s.ytd,150)"></td>
                  <td class="px-4 py-2.5" x-html="ratingBar(s.rating)"></td>
                  <td class="px-4 py-2.5 text-right"><button @click="askAddStock(s)" class="grad rounded-lg text-white px-3 py-1 text-[12px] font-semibold shadow-sm">Add</button></td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </template>
    </div>
  </div>

  <!-- ═══════════ LONG CALLS ═══════════ -->
  <div x-show="activeTab==='longcalls'" x-transition.opacity.duration.200ms class="space-y-6">

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="kpi card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
        <div class="text-slate-500 text-xs font-medium uppercase tracking-wide">Open calls</div>
        <div class="text-3xl font-bold text-slate-900 num mt-2" x-text="lcSummary.count"></div>
      </div>
      <div class="kpi card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
        <div class="text-slate-500 text-xs font-medium uppercase tracking-wide">Premium at risk</div>
        <div class="text-3xl font-bold text-slate-900 num mt-2" x-text="'$'+fmtMoney(lcSummary.risk)"></div>
      </div>
      <div class="kpi card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
        <div class="text-slate-500 text-xs font-medium uppercase tracking-wide">Open P&L</div>
        <div class="text-3xl font-bold num mt-2" :class="retCls(lcSummary.pnl)" x-text="(lcSummary.pnl>=0?'+$':'-$')+fmtMoney(Math.abs(lcSummary.pnl))"></div>
      </div>
      <div class="kpi card bg-white rounded-2xl border shadow-sm p-5" :class="lcSummary.exits?'border-rose-300 ring-1 ring-rose-200':'border-slate-200/80'">
        <div class="text-slate-500 text-xs font-medium uppercase tracking-wide">Exit alerts</div>
        <div class="text-3xl font-bold num mt-2" :class="lcSummary.exits?'text-rose-600':'text-slate-900'" x-text="lcSummary.exits"></div>
      </div>
    </div>

    <template x-if="lcAlerts.length">
      <div class="rounded-2xl border border-rose-300 bg-gradient-to-r from-rose-50 to-red-50 px-5 py-3.5 text-sm text-rose-700 shadow-sm flex items-center gap-2"><span>🔔</span><div><span class="font-semibold" x-text="lcAlerts.length"></span> call(s) flagged to close — <span class="font-semibold" x-text="lcAlerts.map(a=>a.ticker).join(', ')"></span></div></div>
    </template>

    <!-- LC positions -->
    <div>
      <h2 class="text-sm font-semibold text-slate-700 mb-2.5 px-1">Your positions</h2>
      <div class="card bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden overflow-x-auto">
        <table class="w-full border-collapse text-[13px]">
          <thead>
            <tr class="text-[11px] uppercase tracking-wide text-slate-400 border-b border-slate-200">
              <th class="s px-4 py-3 text-left font-semibold" @click="toggleSort('lcpos','ticker')">Ticker<span class="text-indigo-500 ml-0.5" x-text="sortIcon('lcpos','ticker')"></span></th>
              <th class="px-4 py-3 text-right font-semibold">Strike</th>
              <th class="px-4 py-3 text-right font-semibold">Expiry</th>
              <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('lcpos','dte')">DTE<span class="text-indigo-500 ml-0.5" x-text="sortIcon('lcpos','dte')"></span></th>
              <th class="px-4 py-3 text-right font-semibold">Qty</th>
              <th class="px-4 py-3 text-right font-semibold">Entry</th>
              <th class="px-4 py-3 text-right font-semibold">Now</th>
              <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('lcpos','pnl')">P&L $<span class="text-indigo-500 ml-0.5" x-text="sortIcon('lcpos','pnl')"></span></th>
              <th class="px-4 py-3 text-left font-semibold">Action</th>
              <th class="px-4 py-3 text-right font-semibold"></th>
            </tr>
          </thead>
          <tbody>
            <template x-if="lcPosLoading"><tr><td colspan="10" class="px-4 py-8 text-center text-slate-400">Pricing…</td></tr></template>
            <template x-if="!lcPosLoading && lcPositions.length===0"><tr><td colspan="10" class="px-4 py-12 text-center text-slate-400">No long calls — buy one from the candidates below.</td></tr></template>
            <template x-for="p in sortedLcPositions" :key="p.id">
              <tr class="border-b border-slate-100 last:border-0 hover:bg-indigo-50/40" :class="(p.action!=='hold'&&p.action!=='unknown')?'bg-rose-50/60':''">
                <td class="px-4 py-3 text-left font-bold text-slate-900" x-text="p.ticker"></td>
                <td class="px-4 py-3 text-right text-emerald-600 font-medium num" x-text="p.long_strike"></td>
                <td class="px-4 py-3 text-right text-slate-500 num" x-text="p.expiry.slice(5)"></td>
                <td class="px-4 py-3 text-right num" :class="p.dte<=7?'text-rose-600 font-semibold':'text-slate-500'" x-text="p.dte+'d'"></td>
                <td class="px-4 py-3 text-right text-slate-500 num" x-text="(+p.contracts).toFixed(2)"></td>
                <td class="px-4 py-3 text-right text-slate-500 num" x-text="'$'+(+p.entry_debit).toFixed(2)"></td>
                <td class="px-4 py-3 text-right text-slate-800 num" x-text="p.current_mark!=null?'$'+p.current_mark.toFixed(2):'—'"></td>
                <td class="px-4 py-3 text-right num font-semibold" :class="retCls(p.pnl)" x-text="p.pnl!=null?(p.pnl>=0?'+$':'-$')+fmtMoney(Math.abs(p.pnl)):'—'"></td>
                <td class="px-4 py-3 text-left"><span class="rounded-full px-2.5 py-0.5 text-[11px] font-semibold" :class="p.action==='hold'?'bg-emerald-100 text-emerald-700':(p.action==='unknown'?'bg-slate-100 text-slate-400':'bg-rose-100 text-rose-700')" x-text="p.action_label"></span></td>
                <td class="px-4 py-3 text-right"><button @click="askCloseLc(p)" class="rounded-lg border border-slate-300 hover:bg-slate-100 text-slate-700 px-3 py-1 text-[12px] font-medium transition-colors">Close</button></td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>

    <!-- LC candidates -->
    <div>
      <div class="flex items-center justify-between mb-2.5 px-1 flex-wrap gap-2">
        <h2 class="text-sm font-semibold text-slate-700">Candidates <span class="text-slate-400 font-normal">— top 5 by score · momentum + cheapness + % to breakeven</span></h2>
        <div class="inline-flex rounded-lg bg-white border border-slate-200 p-0.5 shadow-sm text-[12px]">
          <button @click="lcMoneyness='itm'" :class="lcMoneyness==='itm'?'px-3 py-1 rounded-md bg-indigo-600 text-white font-semibold':'px-3 py-1 rounded-md text-slate-500 hover:text-slate-800'">ITM ~0.75Δ</button>
          <button @click="lcMoneyness='atm'" :class="lcMoneyness==='atm'?'px-3 py-1 rounded-md bg-indigo-600 text-white font-semibold':'px-3 py-1 rounded-md text-slate-500 hover:text-slate-800'">ATM</button>
          <button @click="lcMoneyness='otm'" :class="lcMoneyness==='otm'?'px-3 py-1 rounded-md bg-indigo-600 text-white font-semibold':'px-3 py-1 rounded-md text-slate-500 hover:text-slate-800'">OTM ~0.35Δ</button>
        </div>
      </div>
      <template x-if="lcStatus==='computing'"><div class="card bg-white rounded-2xl border border-slate-200/80 px-6 py-12 text-center text-slate-400 shadow-sm">Computing candidates… ~30–60s</div></template>
      <template x-if="lcStatus==='error'"><div class="rounded-2xl border border-rose-200 bg-rose-50 px-6 py-6 text-center text-rose-600">Failed to compute candidates.</div></template>
      <template x-if="lcStatus==='ready'">
        <div class="card bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden overflow-x-auto">
          <table class="w-full border-collapse text-[13px]">
            <thead>
              <tr class="text-[11px] uppercase tracking-wide text-slate-400 border-b border-slate-200">
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('lc','rank')">#<span class="text-indigo-500 ml-0.5" x-text="sortIcon('lc','rank')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('lc','score')">Score<span class="text-indigo-500 ml-0.5" x-text="sortIcon('lc','score')"></span></th>
                <th class="s px-3 py-3 text-left font-semibold" @click="toggleSort('lc','ticker')">Ticker<span class="text-indigo-500 ml-0.5" x-text="sortIcon('lc','ticker')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('lc','mom_rank')">Mom#<span class="text-indigo-500 ml-0.5" x-text="sortIcon('lc','mom_rank')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('lc','dte')">DTE<span class="text-indigo-500 ml-0.5" x-text="sortIcon('lc','dte')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('lc','strike')">Strike<span class="text-indigo-500 ml-0.5" x-text="sortIcon('lc','strike')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('lc','premium')">Premium<span class="text-indigo-500 ml-0.5" x-text="sortIcon('lc','premium')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('lc','be_pct')">% to BE<span class="text-indigo-500 ml-0.5" x-text="sortIcon('lc','be_pct')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('lc','exp_move_pct')" title="Expected ±1σ move to expiry (63d vol × √(dte/252))">±1σ move<span class="text-indigo-500 ml-0.5" x-text="sortIcon('lc','exp_move_pct')"></span></th>
                <th class="px-3 py-3 text-right font-semibold">Δ</th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('lc','iv_rank')">IV Rk<span class="text-indigo-500 ml-0.5" x-text="sortIcon('lc','iv_rank')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('lc','rsi')">RSI<span class="text-indigo-500 ml-0.5" x-text="sortIcon('lc','rsi')"></span></th>
                <th class="px-3 py-3 text-right font-semibold" title="Option open interest / 5-day avg stock volume">OI / 5d Vol</th>
                <th class="px-3 py-3 text-right font-semibold"></th>
              </tr>
            </thead>
              <template x-for="c in sortedLongcalls" :key="c.ticker + c.expiry">
                <tbody>
                  <tr class="border-b border-slate-100 hover:bg-indigo-50/40" :class="c.is_top5?'bg-violet-50/60':''">
                    <td class="px-3 py-2.5 text-right text-slate-400 num" x-text="c.rank"></td>
                    <td class="px-3 py-2.5 text-right"><span class="inline-flex items-center justify-center rounded-lg px-2 py-0.5 text-[12px] font-bold num" :class="c.score>=70?'bg-emerald-100 text-emerald-700':c.score>=40?'bg-amber-100 text-amber-700':'bg-slate-100 text-slate-500'" x-text="c.score?.toFixed(0)"></span></td>
                    <td class="px-3 py-2.5 text-left font-bold text-slate-900" :title="c.name||''"><button @click.stop="toggle(c.ticker+'_'+c.expiry)" class="mr-1 text-slate-300 hover:text-indigo-400 transition-colors text-[10px]" x-text="isOpen(c.ticker+'_'+c.expiry)?'▼':'▶'"></button><span x-text="c.ticker"></span><span x-show="c.is_top5" class="ml-1.5 text-[9px] text-white rounded px-1 py-0.5 align-middle font-bold" style="background:#a855f7">TOP 5</span></td>
                    <td class="px-3 py-2.5 text-right text-slate-500 num" x-text="c.mom_rank ?? '—'"></td>
                    <td class="px-3 py-2.5 text-right num">
                      <div class="text-slate-700" x-text="c.dte+'d'"></div>
                      <div class="text-[10px] text-slate-400" x-text="c.expiry"></div>
                    </td>
                    <td class="px-3 py-2.5 text-right text-emerald-600 font-medium num" x-text="c.strike"></td>
                    <td class="px-3 py-2.5 text-right text-slate-700 num" x-text="'$'+c.premium?.toFixed(2)"></td>
                    <td class="px-3 py-2.5 text-right num" :class="beCls(c.be_pct)" x-text="c.be_pct!=null?'+'+c.be_pct.toFixed(1)+'%':'—'"></td>
                    <td class="px-3 py-2.5 text-right text-slate-600 num" :title="c.sig_low!=null?('1σ band: $'+c.sig_low+' – $'+c.sig_high):''" x-text="c.exp_move_pct!=null?'±'+c.exp_move_pct.toFixed(1)+'%':'—'"></td>
                    <td class="px-3 py-2.5 text-right text-slate-500 num" x-text="c.delta!=null?c.delta.toFixed(2):'—'"></td>
                    <td class="px-3 py-2.5 text-right num">
                      <div :class="c.iv_rank==null?'text-slate-300':c.iv_rank<=30?'text-emerald-600 font-medium':c.iv_rank<=60?'text-amber-600 font-medium':'text-rose-600 font-medium'" x-text="c.iv_rank!=null?Math.round(c.iv_rank):'—'"></div>
                      <div class="text-[10px] text-slate-400" x-show="c.iv!=null" x-text="c.iv!=null?(c.iv.toFixed(0)+'% ('+(c.iv_52w_low??'?')+'–'+(c.iv_52w_high??'?')+')'):''"></div>
                    </td>
                    <td class="px-3 py-2.5 text-right num" :class="rsiCls(c.rsi)" x-text="c.rsi ?? '—'"></td>
                    <td class="px-3 py-2.5 text-right num text-slate-500"><span x-text="fmtK(c.oi)"></span><span class="text-slate-300">/</span><span x-text="fmtK(c.volume)"></span></td>
                    <td class="px-3 py-2.5 text-right"><button @click="askOpenLc(c)" class="grad rounded-lg text-white px-3 py-1 text-[12px] font-semibold shadow-sm">Enter</button></td>
                  </tr>
                  <tr x-show="isOpen(c.ticker+'_'+c.expiry)" class="bg-slate-50 border-b border-slate-200">
                    <td colspan="14" class="px-5 py-4">
                      <template x-if="!c.insights">
                        <p class="text-slate-400 text-xs">No insights yet — run <code class="bg-slate-100 px-1 rounded">uv run python refresh.py insights</code></p>
                      </template>
                      <template x-if="c.insights">
                        <div>
                          <div class="flex items-center gap-3 mb-3">
                            <span class="text-[10px] font-bold uppercase px-2 py-0.5 rounded-full" :class="biasCls(c.insights.insights?.bias)" x-text="c.insights.insights?.bias ?? 'neutral'"></span>
                            <span class="text-slate-500 text-xs" x-text="c.insights.insights?.fundamental"></span>
                            <span class="ml-auto text-[10px] text-slate-300" x-text="'as of ' + c.insights.as_of"></span>
                          </div>
                          <div class="grid grid-cols-2 gap-6 mb-3">
                            <div>
                              <div class="text-[10px] font-semibold uppercase tracking-wide text-emerald-600 mb-1.5">Tailwinds</div>
                              <ul class="space-y-1">
                                <template x-for="p in c.insights.insights?.positive ?? []">
                                  <li class="text-xs text-slate-600 flex gap-1.5"><span class="text-emerald-400 mt-px">▲</span><span x-text="p"></span></li>
                                </template>
                              </ul>
                            </div>
                            <div>
                              <div class="text-[10px] font-semibold uppercase tracking-wide text-rose-500 mb-1.5">Risks</div>
                              <ul class="space-y-1">
                                <template x-for="n in c.insights.insights?.negative ?? []">
                                  <li class="text-xs text-slate-600 flex gap-1.5"><span class="text-rose-400 mt-px">▼</span><span x-text="n"></span></li>
                                </template>
                              </ul>
                            </div>
                          </div>
                          <div class="flex items-start justify-between gap-4">
                            <p class="text-[11px] text-slate-400"><span class="font-medium text-slate-500">Key risk:</span> <span x-text="c.insights.insights?.key_risk"></span></p>
                            <div class="flex flex-wrap gap-x-4 gap-y-1 justify-end">
                              <template x-for="a in (c.insights.news ?? []).filter(n=>n.sentiment!=='neutral').slice(0,4)">
                                <a :href="a.url" target="_blank" class="text-[10px] text-indigo-400 hover:text-indigo-600 hover:underline truncate max-w-[220px]" x-text="a.title"></a>
                              </template>
                            </div>
                          </div>
                        </div>
                      </template>
                    </td>
                  </tr>
                </tbody>
              </template>
          </table>
        </div>
      </template>
    </div>
  </div>

  <!-- ═══════════ OPTIONS ═══════════ -->
  <div x-show="activeTab==='options'" x-transition.opacity.duration.200ms class="space-y-6">

    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="kpi card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
        <div class="flex items-center justify-between"><span class="text-slate-500 text-xs font-medium uppercase tracking-wide">Open spreads</span><span class="w-8 h-8 rounded-xl bg-indigo-50 text-indigo-600 flex items-center justify-center"><svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h16M4 18h10"/></svg></span></div>
        <div class="text-3xl font-bold text-slate-900 num mt-2" x-text="optionsSummary.count"></div>
      </div>
      <div class="kpi card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
        <div class="flex items-center justify-between"><span class="text-slate-500 text-xs font-medium uppercase tracking-wide">Capital at risk</span><span class="w-8 h-8 rounded-xl bg-sky-50 text-sky-600 flex items-center justify-center"><svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 8v8m-3-5h6M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg></span></div>
        <div class="text-3xl font-bold text-slate-900 num mt-2" x-text="'$'+fmtMoney(optionsSummary.risk)"></div>
      </div>
      <div class="kpi card bg-white rounded-2xl border border-slate-200/80 shadow-sm p-5">
        <div class="flex items-center justify-between"><span class="text-slate-500 text-xs font-medium uppercase tracking-wide">Open P&L</span><span class="w-8 h-8 rounded-xl flex items-center justify-center" :class="optionsSummary.pnl>=0?'bg-emerald-50 text-emerald-600':'bg-rose-50 text-rose-600'"><svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" :d="optionsSummary.pnl>=0?'M3 17l6-6 4 4 8-8':'M3 7l6 6 4-4 8 8'"/></svg></span></div>
        <div class="text-3xl font-bold num mt-2" :class="retCls(optionsSummary.pnl)" x-text="(optionsSummary.pnl>=0?'+$':'-$')+fmtMoney(Math.abs(optionsSummary.pnl))"></div>
      </div>
      <div class="kpi card bg-white rounded-2xl border shadow-sm p-5" :class="optionsSummary.exits?'border-rose-300 ring-1 ring-rose-200':'border-slate-200/80'">
        <div class="flex items-center justify-between"><span class="text-slate-500 text-xs font-medium uppercase tracking-wide">Exit alerts</span><span class="w-8 h-8 rounded-xl flex items-center justify-center" :class="optionsSummary.exits?'bg-rose-100 text-rose-600':'bg-slate-100 text-slate-400'"><svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01M10.3 4.3 2.6 18a1.5 1.5 0 001.3 2.2h16.2a1.5 1.5 0 001.3-2.2L13.7 4.3a1.5 1.5 0 00-2.6 0z"/></svg></span></div>
        <div class="text-3xl font-bold num mt-2" :class="optionsSummary.exits?'text-rose-600':'text-slate-900'" x-text="optionsSummary.exits"></div>
      </div>
    </div>

    <template x-if="alerts.length">
      <div class="rounded-2xl border border-rose-300 bg-gradient-to-r from-rose-50 to-red-50 px-5 py-3.5 text-sm text-rose-700 shadow-sm flex items-center gap-2"><span>🔔</span><div><span class="font-semibold" x-text="alerts.length"></span> position(s) flagged to close today — <span class="font-semibold" x-text="alerts.map(a=>a.ticker).join(', ')"></span></div></div>
    </template>

    <div>
      <h2 class="text-sm font-semibold text-slate-700 mb-2.5 px-1">Your positions</h2>
      <div class="card bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden overflow-x-auto">
        <table class="w-full border-collapse text-[13px]">
          <thead>
            <tr class="text-[11px] uppercase tracking-wide text-slate-400 border-b border-slate-200">
              <th class="s px-4 py-3 text-left font-semibold" @click="toggleSort('opos','ticker')">Ticker<span class="text-indigo-500 ml-0.5" x-text="sortIcon('opos','ticker')"></span></th>
              <th class="px-4 py-3 text-left font-semibold">Structure</th>
              <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('opos','long_strike')">Long K<span class="text-indigo-500 ml-0.5" x-text="sortIcon('opos','long_strike')"></span></th>
              <th class="px-4 py-3 text-right font-semibold">Short K</th>
              <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('opos','dte')">DTE<span class="text-indigo-500 ml-0.5" x-text="sortIcon('opos','dte')"></span></th>
              <th class="px-4 py-3 text-right font-semibold">Qty</th>
              <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('opos','entry_debit')">Entry<span class="text-indigo-500 ml-0.5" x-text="sortIcon('opos','entry_debit')"></span></th>
              <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('opos','current_mark')">Now<span class="text-indigo-500 ml-0.5" x-text="sortIcon('opos','current_mark')"></span></th>
              <th class="s px-4 py-3 text-right font-semibold" @click="toggleSort('opos','pnl')">P&L $<span class="text-indigo-500 ml-0.5" x-text="sortIcon('opos','pnl')"></span></th>
              <th class="s px-4 py-3 text-left font-semibold" @click="toggleSort('opos','action')">Action<span class="text-indigo-500 ml-0.5" x-text="sortIcon('opos','action')"></span></th>
              <th class="px-4 py-3 text-right font-semibold"></th>
            </tr>
          </thead>
          <tbody>
            <template x-if="posLoading"><tr><td colspan="11" class="px-4 py-8 text-center text-slate-400">Pricing…</td></tr></template>
            <template x-if="!posLoading && positions.length===0"><tr><td colspan="11" class="px-4 py-12 text-center text-slate-400">No open spreads — enter one from the candidates below.</td></tr></template>
            <template x-for="p in sortedPositions" :key="p.id">
              <tr class="border-b border-slate-100 last:border-0 hover:bg-indigo-50/40" :class="(p.action!=='hold'&&p.action!=='unknown')?'bg-rose-50/60':''">
                <td class="px-4 py-3 text-left"><div class="font-bold text-slate-900" x-text="p.ticker"></div><div class="text-[11px] text-slate-400 truncate max-w-[150px]" x-text="nameFor(p.ticker)"></div></td>
                <td class="px-4 py-3 text-left text-slate-500" x-text="p.structure.replace(/_/g,' ')"></td>
                <td class="px-4 py-3 text-right text-emerald-600 font-medium num" x-text="p.long_strike"></td>
                <td class="px-4 py-3 text-right text-slate-600 num" x-text="p.short_strike ?? '—'"></td>
                <td class="px-4 py-3 text-right num" :class="p.dte<=7?'text-rose-600 font-semibold':'text-slate-500'" x-text="p.dte+'d'"></td>
                <td class="px-4 py-3 text-right text-slate-500 num" x-text="(+p.contracts).toFixed(2)"></td>
                <td class="px-4 py-3 text-right text-slate-500 num" x-text="'$'+(+p.entry_debit).toFixed(2)"></td>
                <td class="px-4 py-3 text-right text-slate-800 num" x-text="p.current_mark!=null?'$'+p.current_mark.toFixed(2):'—'"></td>
                <td class="px-4 py-3 text-right num font-semibold" :class="retCls(p.pnl)" x-text="p.pnl!=null?(p.pnl>=0?'+$':'-$')+fmtMoney(Math.abs(p.pnl)):'—'"></td>
                <td class="px-4 py-3 text-left"><span class="rounded-full px-2.5 py-0.5 text-[11px] font-semibold" :class="p.action==='hold'?'bg-emerald-100 text-emerald-700':(p.action==='unknown'?'bg-slate-100 text-slate-400':'bg-rose-100 text-rose-700')" x-text="p.action_label"></span></td>
                <td class="px-4 py-3 text-right"><button @click="askClose(p)" class="rounded-lg border border-slate-300 hover:bg-slate-100 text-slate-700 px-3 py-1 text-[12px] font-medium transition-colors">Close</button></td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>

    <div>
      <h2 class="text-sm font-semibold text-slate-700 mb-2.5 px-1">Candidates <span class="text-slate-400 font-normal">— top 5 by score · click any column to sort</span></h2>
      <template x-if="candStatus==='computing'"><div class="card bg-white rounded-2xl border border-slate-200/80 px-6 py-12 text-center text-slate-400 shadow-sm">Computing candidates (momentum · chains · IV rank)… ~30–60s</div></template>
      <template x-if="candStatus==='error'"><div class="rounded-2xl border border-rose-200 bg-rose-50 px-6 py-6 text-center text-rose-600">Failed to compute candidates.</div></template>
      <template x-if="candStatus==='ready'">
        <div class="card bg-white rounded-2xl border border-slate-200/80 shadow-sm overflow-hidden overflow-x-auto">
          <table class="w-full border-collapse text-[13px]">
            <thead>
              <tr class="text-[11px] uppercase tracking-wide text-slate-400 border-b border-slate-200">
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('cand','rank')">#<span class="text-indigo-500 ml-0.5" x-text="sortIcon('cand','rank')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('cand','score')">Score<span class="text-indigo-500 ml-0.5" x-text="sortIcon('cand','score')"></span></th>
                <th class="s px-3 py-3 text-left font-semibold" @click="toggleSort('cand','ticker')">Ticker<span class="text-indigo-500 ml-0.5" x-text="sortIcon('cand','ticker')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('cand','dte')">DTE<span class="text-indigo-500 ml-0.5" x-text="sortIcon('cand','dte')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('cand','long_strike')">Long K<span class="text-indigo-500 ml-0.5" x-text="sortIcon('cand','long_strike')"></span></th>
                <th class="px-3 py-3 text-right font-semibold">Short K</th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('cand','short_oi')" title="Open interest L/S">OI L/S<span class="text-indigo-500 ml-0.5" x-text="sortIcon('cand','short_oi')"></span></th>
                <th class="px-3 py-3 text-right font-semibold" title="5-day avg stock volume">5d Vol</th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('cand','cost')">Cost<span class="text-indigo-500 ml-0.5" x-text="sortIcon('cand','cost')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('cand','max_profit')">Max P<span class="text-indigo-500 ml-0.5" x-text="sortIcon('cand','max_profit')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('cand','rr')">R/R<span class="text-indigo-500 ml-0.5" x-text="sortIcon('cand','rr')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('cand','win_prob')" title="BSM probability the stock closes above breakeven at expiry">Win%<span class="text-indigo-500 ml-0.5" x-text="sortIcon('cand','win_prob')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('cand','be_pct')" title="Breakeven price and % move needed">Breakeven<span class="text-indigo-500 ml-0.5" x-text="sortIcon('cand','be_pct')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('cand','iv_rank')">IV Rk<span class="text-indigo-500 ml-0.5" x-text="sortIcon('cand','iv_rank')"></span></th>
                <th class="s px-3 py-3 text-right font-semibold" @click="toggleSort('cand','rsi')">RSI<span class="text-indigo-500 ml-0.5" x-text="sortIcon('cand','rsi')"></span></th>
                <th class="px-3 py-3 text-right font-semibold"></th>
              </tr>
            </thead>
              <template x-for="c in sortedCandidates" :key="c.ticker + c.expiry">
                <tbody>
                  <tr class="border-b border-slate-100 hover:bg-indigo-50/40" :class="c.is_top5?'bg-indigo-50/50':''">
                    <td class="px-3 py-2.5 text-right text-slate-400 num" x-text="c.rank"></td>
                    <td class="px-3 py-2.5 text-right"><span class="inline-flex items-center justify-center rounded-lg px-2 py-0.5 text-[12px] font-bold num" :class="c.score>=70?'bg-emerald-100 text-emerald-700':c.score>=40?'bg-amber-100 text-amber-700':'bg-slate-100 text-slate-500'" x-text="c.score?.toFixed(0)"></span></td>
                    <td class="px-3 py-2.5 text-left font-bold text-slate-900" :title="c.name||''"><button @click.stop="toggle(c.ticker+'_'+c.expiry)" class="mr-1 text-slate-300 hover:text-indigo-400 transition-colors text-[10px]" x-text="isOpen(c.ticker+'_'+c.expiry)?'▼':'▶'"></button><span x-text="c.ticker"></span><span x-show="c.is_top5" class="ml-1.5 text-[9px] text-white grad rounded px-1 py-0.5 align-middle font-bold">TOP 5</span></td>
                    <td class="px-3 py-2.5 text-right text-slate-500 num" x-text="c.dte+'d'"></td>
                    <td class="px-3 py-2.5 text-right text-emerald-600 font-medium num" x-text="c.long_strike"></td>
                    <td class="px-3 py-2.5 text-right text-slate-600 num" x-text="c.short_strike"></td>
                    <td class="px-3 py-2.5 text-right num text-slate-500"><span x-text="fmtK(c.long_oi)"></span><span class="text-slate-300">/</span><span :class="c.short_oi<100?'text-rose-500 font-medium':''" x-text="fmtK(c.short_oi)"></span></td>
                    <td class="px-3 py-2.5 text-right num text-slate-500"><span x-text="fmtK(c.long_vol)"></span><span class="text-slate-300">/</span><span x-text="fmtK(c.short_vol)"></span></td>
                    <td class="px-3 py-2.5 text-right text-slate-600 num" x-text="'$'+c.cost"></td>
                    <td class="px-3 py-2.5 text-right text-emerald-600 num" x-text="'$'+c.max_profit"></td>
                    <td class="px-3 py-2.5 text-right text-slate-900 font-bold num" x-text="c.rr?.toFixed(1)+'x'"></td>
                    <td class="px-3 py-2.5 text-right num font-medium" :class="c.win_prob>=50?'text-emerald-600':c.win_prob>=35?'text-amber-600':'text-rose-600'" x-text="c.win_prob!=null?c.win_prob.toFixed(0)+'%':'—'"></td>
                    <td class="px-3 py-2.5 text-right num"><span class="text-slate-600" x-text="c.breakeven!=null?c.breakeven.toFixed(2):'—'"></span><span class="ml-1 text-[11px] font-medium" :class="beCls(c.be_pct)" x-text="c.be_pct!=null?'+'+c.be_pct.toFixed(1)+'%':''"></span></td>
                    <td class="px-3 py-2.5 text-right num" :class="c.iv_rank==null?'text-slate-300':c.iv_rank<=30?'text-emerald-600':c.iv_rank<=60?'text-amber-600':'text-rose-600'" x-text="c.iv_rank!=null?Math.round(c.iv_rank):'—'"></td>
                    <td class="px-3 py-2.5 text-right num" :class="rsiCls(c.rsi)" x-text="c.rsi ?? '—'"></td>
                    <td class="px-3 py-2.5 text-right"><button @click="askOpen(c)" class="grad rounded-lg text-white px-3 py-1 text-[12px] font-semibold shadow-sm">Enter</button></td>
                  </tr>
                  <tr x-show="isOpen(c.ticker+'_'+c.expiry)" class="bg-slate-50 border-b border-slate-200">
                    <td colspan="16" class="px-5 py-4">
                      <template x-if="!c.insights">
                        <p class="text-slate-400 text-xs">No insights yet — run <code class="bg-slate-100 px-1 rounded">uv run python refresh.py insights</code></p>
                      </template>
                      <template x-if="c.insights">
                        <div>
                          <div class="flex items-center gap-3 mb-3">
                            <span class="text-[10px] font-bold uppercase px-2 py-0.5 rounded-full" :class="biasCls(c.insights.insights?.bias)" x-text="c.insights.insights?.bias ?? 'neutral'"></span>
                            <span class="text-slate-500 text-xs" x-text="c.insights.insights?.fundamental"></span>
                            <span class="ml-auto text-[10px] text-slate-300" x-text="'as of ' + c.insights.as_of"></span>
                          </div>
                          <div class="grid grid-cols-2 gap-6 mb-3">
                            <div>
                              <div class="text-[10px] font-semibold uppercase tracking-wide text-emerald-600 mb-1.5">Tailwinds</div>
                              <ul class="space-y-1">
                                <template x-for="p in c.insights.insights?.positive ?? []">
                                  <li class="text-xs text-slate-600 flex gap-1.5"><span class="text-emerald-400 mt-px">▲</span><span x-text="p"></span></li>
                                </template>
                              </ul>
                            </div>
                            <div>
                              <div class="text-[10px] font-semibold uppercase tracking-wide text-rose-500 mb-1.5">Risks</div>
                              <ul class="space-y-1">
                                <template x-for="n in c.insights.insights?.negative ?? []">
                                  <li class="text-xs text-slate-600 flex gap-1.5"><span class="text-rose-400 mt-px">▼</span><span x-text="n"></span></li>
                                </template>
                              </ul>
                            </div>
                          </div>
                          <div class="flex items-start justify-between gap-4">
                            <p class="text-[11px] text-slate-400"><span class="font-medium text-slate-500">Key risk:</span> <span x-text="c.insights.insights?.key_risk"></span></p>
                            <div class="flex flex-wrap gap-x-4 gap-y-1 justify-end">
                              <template x-for="a in (c.insights.news ?? []).filter(n=>n.sentiment!=='neutral').slice(0,4)">
                                <a :href="a.url" target="_blank" class="text-[10px] text-indigo-400 hover:text-indigo-600 hover:underline truncate max-w-[220px]" x-text="a.title"></a>
                              </template>
                            </div>
                          </div>
                        </div>
                      </template>
                    </td>
                  </tr>
                </tbody>
              </template>
          </table>
        </div>
      </template>
    </div>
  </div>

  <!-- ═══════════ MODAL ═══════════ -->
  <template x-if="modal">
    <div class="fixed inset-0 bg-slate-900/40 backdrop-blur-sm flex items-center justify-center z-50" @click.self="modal=null" x-transition.opacity>
      <div class="bg-white border border-slate-200 rounded-2xl p-6 w-[380px] shadow-2xl">
        <template x-if="modal.mode==='open'">
          <div>
            <h3 class="text-slate-900 font-bold text-lg mb-1">Enter <span x-text="modal.row.ticker"></span> bull spread</h3>
            <p class="text-slate-500 text-xs mb-4"><span x-text="modal.row.long_strike"></span>/<span x-text="modal.row.short_strike"></span> · exp <span x-text="modal.row.expiry"></span></p>
            <label class="block text-xs text-slate-500 mb-1 font-medium">Contracts</label>
            <input x-model="modal.contracts" type="number" step="1" min="1" class="w-full bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm mb-3 num focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 outline-none">
            <label class="block text-xs text-slate-500 mb-1 font-medium">Fill debit (per share)</label>
            <input x-model="modal.fill" type="number" step="0.01" class="w-full bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm mb-1 num focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 outline-none">
            <p class="text-slate-400 text-[11px] mb-4">Risk ≈ $<span x-text="(modal.fill*modal.contracts*100).toFixed(0)"></span></p>
            <div class="flex gap-2 justify-end"><button @click="modal=null" class="px-3 py-1.5 text-sm text-slate-500 hover:text-slate-800">Cancel</button><button @click="confirmOpen()" class="grad px-4 py-1.5 text-sm rounded-lg text-white font-semibold shadow-sm">Confirm entry</button></div>
          </div>
        </template>
        <template x-if="modal.mode==='close'">
          <div>
            <h3 class="text-slate-900 font-bold text-lg mb-1">Close <span x-text="modal.pos.ticker"></span></h3>
            <p class="text-slate-500 text-xs mb-4" x-text="modal.pos.action_label"></p>
            <label class="block text-xs text-slate-500 mb-1 font-medium">Exit price (per share)</label>
            <input x-model="modal.fill" type="number" step="0.01" class="w-full bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm mb-4 num focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 outline-none">
            <div class="flex gap-2 justify-end"><button @click="modal=null" class="px-3 py-1.5 text-sm text-slate-500 hover:text-slate-800">Cancel</button><button @click="confirmClose()" class="px-4 py-1.5 text-sm rounded-lg bg-rose-600 hover:bg-rose-500 text-white font-semibold">Confirm close</button></div>
          </div>
        </template>
        <template x-if="modal.mode==='stock_open'">
          <div>
            <h3 class="text-slate-900 font-bold text-lg mb-1">Buy <span x-text="modal.row.ticker"></span></h3>
            <p class="text-slate-500 text-xs mb-4" x-text="modal.row.name||('momentum rank #'+modal.row.rank)"></p>
            <label class="block text-xs text-slate-500 mb-1 font-medium">Shares</label>
            <input x-model="modal.shares" type="number" step="1" min="1" class="w-full bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm mb-3 num focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 outline-none">
            <label class="block text-xs text-slate-500 mb-1 font-medium">Fill price</label>
            <input x-model="modal.fill" type="number" step="0.01" class="w-full bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm mb-1 num focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 outline-none">
            <p class="text-slate-400 text-[11px] mb-4">Cost ≈ $<span x-text="fmtMoney(modal.fill*modal.shares)"></span></p>
            <div class="flex gap-2 justify-end"><button @click="modal=null" class="px-3 py-1.5 text-sm text-slate-500 hover:text-slate-800">Cancel</button><button @click="confirmAddStock()" class="grad px-4 py-1.5 text-sm rounded-lg text-white font-semibold shadow-sm">Confirm buy</button></div>
          </div>
        </template>
        <template x-if="modal.mode==='stock_close'">
          <div>
            <h3 class="text-slate-900 font-bold text-lg mb-1">Sell <span x-text="modal.pos.ticker"></span></h3>
            <p class="text-slate-500 text-xs mb-4"><span x-text="(+modal.pos.shares).toFixed(0)"></span> shares</p>
            <label class="block text-xs text-slate-500 mb-1 font-medium">Exit price</label>
            <input x-model="modal.fill" type="number" step="0.01" class="w-full bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm mb-1 num focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 outline-none">
            <p class="text-slate-400 text-[11px] mb-4">Realized ≈ <span :class="retCls(modal.fill-modal.pos.entry_price)" x-text="((modal.fill-modal.pos.entry_price)*modal.pos.shares>=0?'+$':'-$')+fmtMoney(Math.abs((modal.fill-modal.pos.entry_price)*modal.pos.shares))"></span></p>
            <div class="flex gap-2 justify-end"><button @click="modal=null" class="px-3 py-1.5 text-sm text-slate-500 hover:text-slate-800">Cancel</button><button @click="confirmCloseStock()" class="px-4 py-1.5 text-sm rounded-lg bg-rose-600 hover:bg-rose-500 text-white font-semibold">Confirm sell</button></div>
          </div>
        </template>

        <template x-if="modal.mode==='lc_open'">
          <div>
            <h3 class="text-slate-900 font-bold text-lg mb-1">Buy <span x-text="modal.row.ticker"></span> call</h3>
            <p class="text-slate-500 text-xs mb-4"><span x-text="modal.row.strike"></span>C · exp <span x-text="modal.row.expiry"></span> · breakeven <span x-text="modal.row.breakeven"></span> (+<span x-text="modal.row.be_pct"></span>%)</p>
            <label class="block text-xs text-slate-500 mb-1 font-medium">Contracts</label>
            <input x-model="modal.contracts" type="number" step="1" min="1" class="w-full bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm mb-3 num focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 outline-none">
            <label class="block text-xs text-slate-500 mb-1 font-medium">Fill premium (per share)</label>
            <input x-model="modal.fill" type="number" step="0.01" class="w-full bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm mb-1 num focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 outline-none">
            <p class="text-slate-400 text-[11px] mb-4">Cost / max loss ≈ $<span x-text="(modal.fill*modal.contracts*100).toFixed(0)"></span></p>
            <div class="flex gap-2 justify-end"><button @click="modal=null" class="px-3 py-1.5 text-sm text-slate-500 hover:text-slate-800">Cancel</button><button @click="confirmOpenLc()" class="grad px-4 py-1.5 text-sm rounded-lg text-white font-semibold shadow-sm">Confirm buy</button></div>
          </div>
        </template>

        <template x-if="modal.mode==='lc_close'">
          <div>
            <h3 class="text-slate-900 font-bold text-lg mb-1">Close <span x-text="modal.pos.ticker"></span> call</h3>
            <p class="text-slate-500 text-xs mb-4" x-text="modal.pos.action_label"></p>
            <label class="block text-xs text-slate-500 mb-1 font-medium">Exit premium (per share)</label>
            <input x-model="modal.fill" type="number" step="0.01" class="w-full bg-white border border-slate-300 rounded-lg px-3 py-2 text-sm mb-4 num focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 outline-none">
            <div class="flex gap-2 justify-end"><button @click="modal=null" class="px-3 py-1.5 text-sm text-slate-500 hover:text-slate-800">Cancel</button><button @click="confirmCloseLc()" class="px-4 py-1.5 text-sm rounded-lg bg-rose-600 hover:bg-rose-500 text-white font-semibold">Confirm close</button></div>
          </div>
        </template>
      </div>
    </div>
  </template>
</div>

<script>
function dash() {
  return {
    activeTab: 'portfolio', asOf: '', modal: null, expanded: {},
    portfolio: null,
    positions: [], posLoading: true, candidates: [], candStatus: 'loading',
    stockPositions: [], stockPosLoading: true, stockRotation: { dropouts: [], new_entrants: [] },
    stocks: [], stockStatus: 'loading',
    lcByM: { atm:[], itm:[], otm:[] }, lcMoneyness: 'atm', lcStatus: 'loading', lcPositions: [], lcPosLoading: true,
    sort: { stocks:{key:'rank',dir:1}, cand:{key:'rank',dir:1}, spos:{key:'ticker',dir:1}, opos:{key:'ticker',dir:1}, lc:{key:'rank',dir:1}, lcpos:{key:'ticker',dir:1} },

    async init() { this.loadPortfolio(); await Promise.all([this.loadStockPositions(), this.loadPositions(), this.loadLcPositions()]); this.loadStockCandidates(); this.loadCandidates(); this.loadLcCandidates(); },

    tabCls(t) { return this.activeTab===t ? 'px-4 py-1.5 text-sm rounded-lg bg-indigo-600 text-white font-semibold shadow-sm' : 'px-4 py-1.5 text-sm rounded-lg text-slate-500 hover:text-slate-800 font-medium'; },

    // sorting
    toggleSort(g,key){ const s=this.sort[g]; if(s.key===key){ s.dir*=-1; } else { s.key=key; s.dir=1; } },
    sortIcon(g,key){ const s=this.sort[g]; return s.key!==key ? '' : (s.dir>0?'↑':'↓'); },
    _val(row,key){ if(key==='rating'){ const r=row.rating; return (r&&r.total)? r.buy/r.total : null; } return row[key]; },
    _sorted(arr,g){ const s=this.sort[g]; if(!s.key) return arr; return [...arr].sort((a,b)=>{
        let av=this._val(a,s.key), bv=this._val(b,s.key);
        if(av==null&&bv==null) return 0; if(av==null) return 1; if(bv==null) return -1;
        const c=(typeof av==='number'&&typeof bv==='number')? av-bv : String(av).localeCompare(String(bv));
        return c*s.dir; }); },
    get sortedStocks(){ return this._sorted(this.stocks,'stocks'); },
    get sortedCandidates(){ return this._sorted(this.candidates,'cand'); },
    get sortedStockPositions(){ return this._sorted(this.stockPositions,'spos'); },
    get sortedPositions(){ return this._sorted(this.positions,'opos'); },
    get longcalls(){ return this.lcByM[this.lcMoneyness] || []; },
    get sortedLongcalls(){ return this._sorted(this.longcalls,'lc'); },
    get sortedLcPositions(){ return this._sorted(this.lcPositions,'lcpos'); },
    nameFor(t){ const s=this.stocks.find(x=>x.ticker===t); return (s&&s.name)?s.name:''; },

    // formatting / viz
    pct(v)    { return v==null ? '—' : (v>=0?'+':'')+v.toFixed(1)+'%'; },
    retCls(v) { return v==null ? 'text-slate-400' : (v>=0 ? 'text-emerald-600' : 'text-rose-600'); },
    rsiCls(r) { return r==null ? 'text-slate-300' : r>=75 ? 'text-rose-600' : r>=50 ? 'text-emerald-600' : 'text-slate-400'; },
    rsiBar(r) { return r==null ? 'bg-slate-200' : r>=75 ? 'bg-rose-400' : r>=50 ? 'bg-emerald-400' : 'bg-slate-300'; },
    beCls(p)  { return p==null ? 'text-slate-300' : p<2 ? 'text-emerald-600' : p<5 ? 'text-amber-600' : 'text-rose-600'; },
    fmtK(v)   { if (v==null || v===0) return '—'; return v>=1e6 ? (v/1e6).toFixed(1)+'M' : v>=1000 ? Math.round(v/1000)+'K' : String(v); },
    fmtMoney(v){ v=v||0; return v>=1e6?(v/1e6).toFixed(2)+'M':v>=1e3?Math.round(v).toLocaleString():v.toFixed(0); },
    fmtCap(v){ if(v==null) return '—'; return v>=1e12?'$'+(v/1e12).toFixed(2)+'T':v>=1e9?'$'+(v/1e9).toFixed(0)+'B':'$'+(v/1e6).toFixed(0)+'M'; },
    barW(v, cap) { if (v==null) return 0; return Math.min(100, Math.abs(v)/cap*100); },
    bar(v, cap) {
      if (v==null) return '<div class="text-right text-slate-300 num text-xs">—</div>';
      const w = Math.min(50, Math.abs(v)/cap*50), pos = v>=0;
      const fill = pos ? '#34d399' : '#fb7185', txt = pos ? '#059669' : '#e11d48';
      const side = pos ? `left:50%;width:${w}%` : `right:50%;width:${w}%`;
      return `<div class="relative h-5 num text-xs"><div class="absolute top-0 bottom-0" style="left:50%;width:1px;background:#e2e8f0"></div>`
           + `<div class="absolute top-1 bottom-1 rounded-sm" style="${side};background:${fill};opacity:.55"></div>`
           + `<div class="absolute inset-0 flex items-center justify-end pr-1 font-medium" style="color:${txt}">${(pos?'+':'')+v.toFixed(1)+'%'}</div></div>`;
    },
    ratingBar(r){
      if(!r || !r.total) return '<span class="text-slate-300 text-xs">—</span>';
      const bp=r.buy/r.total*100, hp=r.hold/r.total*100, sp=r.sell/r.total*100;
      const title=`${r.consensus||''} · ${r.buy} buy / ${r.hold} hold / ${r.sell} sell (${r.total} analysts)`;
      return `<div class="flex items-center gap-2" title="${title}"><div class="flex h-2 w-20 rounded-full overflow-hidden bg-slate-100">`
        + `<div style="width:${bp}%;background:#34d399"></div><div style="width:${hp}%;background:#cbd5e1"></div><div style="width:${sp}%;background:#fb7185"></div>`
        + `</div><span class="text-xs text-emerald-600 num font-semibold">${Math.round(bp)}%</span></div>`;
    },

    toggle(key) { this.expanded = {...this.expanded, [key]: !this.expanded[key]}; },
    isOpen(key) { return !!this.expanded[key]; },
    biasCls(b) { return b==='bullish'?'bg-emerald-100 text-emerald-700':b==='bearish'?'bg-rose-100 text-rose-700':'bg-slate-100 text-slate-500'; },

    get alerts() { return this.positions.filter(p => p.action!=='hold' && p.action!=='unknown'); },
    get stockSummary() { const ps=this.stockPositions; const pnl=ps.reduce((a,p)=>a+(p.pnl||0),0), val=ps.reduce((a,p)=>a+((p.current_price||0)*p.shares),0), cost=ps.reduce((a,p)=>a+(p.entry_price*p.shares),0); return { count: ps.length, pnl, val, pnlPct: cost>0?pnl/cost*100:0, rotate: ps.filter(p=>p.action==='rotate').length }; },
    get optionsSummary() { const ps=this.positions; return { count: ps.length, pnl: ps.reduce((a,p)=>a+(p.pnl||0),0), risk: ps.reduce((a,p)=>a+(p.entry_debit*p.contracts*100),0), exits: this.alerts.length }; },
    get lcAlerts() { return this.lcPositions.filter(p => p.action!=='hold' && p.action!=='unknown'); },
    get lcSummary() { const ps=this.lcPositions; return { count: ps.length, pnl: ps.reduce((a,p)=>a+(p.pnl||0),0), risk: ps.reduce((a,p)=>a+(p.entry_debit*p.contracts*100),0), exits: this.lcAlerts.length }; },

    async loadPortfolio() { try { this.portfolio = await (await fetch('/api/portfolio')).json(); } catch(e){ this.portfolio=null; } },
    bucketColor(n){ return n==='momentum'?'#6366f1':n==='core'?'#0ea5e9':n==='long_calls'?'#a855f7':'#f59e0b'; },
    bucketLabel(n){ return n==='momentum'?'Momentum':n==='core'?'Core / ETFs':n==='long_calls'?'Long Calls':'Spreads'; },
    async tagStock(p, tracker){ await fetch('/api/stocks/tag',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:p.id,tracker})}); await this.loadStockPositions(); this.loadPortfolio(); },
    equityChart(hist){
      const W=560,H=120,P=6; const vals=hist.map(h=>h.total); const min=Math.min(...vals),max=Math.max(...vals);
      const span=(max-min)||1; const n=hist.length;
      const x=i=> P + i*(W-2*P)/(n-1); const y=v=> P + (1-(v-min)/span)*(H-2*P);
      let line=hist.map((h,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(h.total).toFixed(1)}`).join(' ');
      const area=`${line} L${x(n-1).toFixed(1)},${H-P} L${x(0).toFixed(1)},${H-P} Z`;
      const up = vals[n-1]>=vals[0]; const c = up?'#10b981':'#ef4444';
      return `<svg viewBox="0 0 ${W} ${H}" class="w-full" style="height:120px"><defs><linearGradient id="eg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${c}" stop-opacity=".18"/><stop offset="100%" stop-color="${c}" stop-opacity="0"/></linearGradient></defs>`
        + `<path d="${area}" fill="url(#eg)"/><path d="${line}" fill="none" stroke="${c}" stroke-width="2" stroke-linejoin="round"/></svg>`;
    },
    async loadPositions() { this.posLoading=true; try { this.positions=(await (await fetch('/api/positions')).json()).positions||[]; } catch(e){ this.positions=[]; } this.posLoading=false; },
    async loadStockPositions() { this.stockPosLoading=true; try { const d=await (await fetch('/api/stocks/positions')).json(); this.stockPositions=d.positions||[]; this.stockRotation=d.rotation||{dropouts:[],new_entrants:[]}; } catch(e){ this.stockPositions=[]; } this.stockPosLoading=false; },
    async loadCandidates() { try { const d=await (await fetch('/api/candidates')).json(); if(d.status==='ready'){ this.candidates=d.rows; this.asOf=d.as_of; this.candStatus='ready'; } else if(d.status==='computing'){ this.candStatus='computing'; setTimeout(()=>this.loadCandidates(),3000); } else { this.candStatus='error'; } } catch(e){ this.candStatus='error'; } },
    async loadStockCandidates() { try { const d=await (await fetch('/api/stocks/candidates')).json(); if(d.status==='ready'){ this.stocks=d.stocks; this.asOf=d.as_of; this.stockStatus='ready'; this.loadStockPositions(); } else if(d.status==='computing'){ this.stockStatus='computing'; setTimeout(()=>this.loadStockCandidates(),3000); } else { this.stockStatus='error'; } } catch(e){ this.stockStatus='error'; } },
    async loadLcPositions() { this.lcPosLoading=true; try { this.lcPositions=(await (await fetch('/api/longcalls/positions')).json()).positions||[]; } catch(e){ this.lcPositions=[]; } this.lcPosLoading=false; },
    async loadLcCandidates() { try { const d=await (await fetch('/api/longcalls/candidates')).json(); if(d.status==='ready'){ this.lcByM=d.longcalls; this.asOf=d.as_of; this.lcStatus='ready'; this.loadLcPositions(); } else if(d.status==='computing'){ this.lcStatus='computing'; setTimeout(()=>this.loadLcCandidates(),3000); } else { this.lcStatus='error'; } } catch(e){ this.lcStatus='error'; } },
    async refreshAll() { this.candStatus='computing'; this.stockStatus='computing'; this.lcStatus='computing'; this.loadPortfolio(); await fetch('/api/candidates/refresh',{method:'POST'}); setTimeout(()=>{ this.loadCandidates(); this.loadStockCandidates(); this.loadLcCandidates(); this.loadPortfolio(); },3000); },
    askOpenLc(row){ this.modal={mode:'lc_open',row,contracts:1,fill:row.premium}; },
    askCloseLc(p){ this.modal={mode:'lc_close',pos:p,fill:p.current_mark??p.entry_debit}; },
    async confirmOpenLc(){ const m=this.modal; await fetch('/api/longcalls/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticker:m.row.ticker,expiry:m.row.expiry,strike:m.row.strike,premium:parseFloat(m.fill),contracts:parseFloat(m.contracts)})}); this.modal=null; await this.loadLcPositions(); this.loadPortfolio(); },
    async confirmCloseLc(){ const m=this.modal; await fetch('/api/longcalls/close',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:m.pos.id,exit_premium:parseFloat(m.fill)})}); this.modal=null; await this.loadLcPositions(); this.loadPortfolio(); },

    askOpen(row){ this.modal={mode:'open',row,contracts:1,fill:row.debit}; },
    askClose(p){ this.modal={mode:'close',pos:p,fill:p.current_mark??p.entry_debit}; },
    async confirmOpen(){ const m=this.modal; await fetch('/api/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticker:m.row.ticker,structure:'bull_spread',expiry:m.row.expiry,long_strike:m.row.long_strike,short_strike:m.row.short_strike,width:m.row.width,entry_debit:parseFloat(m.fill),contracts:parseFloat(m.contracts)})}); this.modal=null; await this.loadPositions(); this.loadPortfolio(); },
    async confirmClose(){ const m=this.modal; await fetch('/api/close',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:m.pos.id,exit_debit:parseFloat(m.fill)})}); this.modal=null; await this.loadPositions(); this.loadPortfolio(); },
    askAddStock(row){ this.modal={mode:'stock_open',row,shares:10,fill:row.price}; },
    askCloseStock(p){ this.modal={mode:'stock_close',pos:p,fill:p.current_price??p.entry_price}; },
    async confirmAddStock(){ const m=this.modal; await fetch('/api/stocks/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticker:m.row.ticker,shares:parseFloat(m.shares),fill_price:parseFloat(m.fill)})}); this.modal=null; await this.loadStockPositions(); this.loadPortfolio(); },
    async confirmCloseStock(){ const m=this.modal; await fetch('/api/stocks/close',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:m.pos.id,exit_price:parseFloat(m.fill)})}); this.modal=null; await this.loadStockPositions(); this.loadPortfolio(); },
  };
}
</script>
</body>
</html>"""
