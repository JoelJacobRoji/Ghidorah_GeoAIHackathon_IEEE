import { useApi } from '../lib/api'
import { fmtIst } from '../lib/format'
import { ErrorState, Skeleton } from './ui'

/* eslint-disable @typescript-eslint/no-explicit-any */
type Report = Record<string, any>

const f3 = (x: number | null | undefined) => (x === null || x === undefined ? '—' : x.toFixed(3))
const pct = (x: number | null | undefined) => (x === null || x === undefined ? '—' : `${(x * 100).toFixed(1)} %`)

export function MlReport() {
  const { data: r, error, loading, refresh } = useApi<Report>('/api/ml/report')
  if (loading && !r) return <Skeleton className="h-48" />
  if (error && !r)
    return error.status === 404 ? (
      <p className="rounded-xl bg-ivory/60 p-4 text-[14px]">
        The model has not been trained yet. It is trained on the Feb–May 2025 season and tested once on 2026, after both seasons' data are complete
        (<code className="font-mono">python -m incendium ml-train</code>).
      </p>
    ) : (
      <ErrorState error={error} onRetry={refresh} />
    )
  if (!r) return null
  const rows: [string, string, any][] = [
    ['Gradient-boosted trees', 'gbt', r.models.gbt],
    ['Logistic regression', 'logistic', r.models.logistic],
    ['Gradient-boosted trees without fire history', 'gbt_without_history', r.models.gbt_without_history],
    ['Baseline: FWI alone', 'fwi', r.baselines.fwi],
    ['Baseline: fire history alone', 'hist', r.baselines.hist_rate],
  ]
  const best = r.models[r.selected_for_display]
  const maxDrop = Math.max(0.001, ...r.permutation_importance_test.map((x: any) => x.auc_drop))
  return (
    <div className="space-y-4">
      <p className="rounded-lg bg-[#3d5a6a]/12 p-3 text-[14px] text-[#24404d]">
        <b>What it predicts:</b> {r.target}. Satellite detections include crop and other managed burning, so this is a model of detected burning activity, not of wildfire ignition.
        Trained on {r.train.period[0]} → {r.train.period[1]} ({r.train.cell_days.toLocaleString()} cell-days), tested once on {r.test.period[0]} → {r.test.period[1]}. Generated {fmtIst(r.generated_at)}.
      </p>
      <div className="overflow-x-auto rounded-xl border border-graphite-900/10 bg-ivory/60 p-4">
        <div className="text-sm font-semibold">Out-of-sample test (2026)</div>
        <table className="mt-2 w-full min-w-[560px] text-left text-[12.5px]">
          <thead className="text-graphite-600"><tr><th className="py-1 pr-3 font-medium">Model</th><th className="py-1 pr-3 font-medium">AUC</th><th className="py-1 pr-3 font-medium">95 % CI (cell bootstrap)</th><th className="py-1 pr-3 font-medium">AUC within month</th><th className="py-1 font-medium">Brier skill vs constant</th></tr></thead>
          <tbody className="font-mono tabular">
            {rows.filter(([, , v]) => v).map(([label, key, v]) => (
              <tr key={key} className={`border-t border-graphite-900/8 ${key === r.selected_for_display ? 'font-semibold' : ''}`}>
                <td className="py-1 pr-3 font-sans">{label}{key === r.selected_for_display ? ' (shown on map)' : ''}</td>
                <td className="py-1 pr-3">{f3(v.auc)}</td>
                <td className="py-1 pr-3">{f3(v.ci_low)} – {f3(v.ci_high)}</td>
                <td className="py-1 pr-3">{f3(v.auc_within_month)}</td>
                <td className="py-1">{v.brier_skill_vs_climatology === undefined ? '—' : f3(v.brier_skill_vs_climatology)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-[12px] text-graphite-600">
          Base rate: {pct(r.train.base_rate)} of cell-days in 2025, {pct(r.test.base_rate)} in 2026. The model is useful only if it beats both baselines; the row without fire history shows how much the weather and vegetation inputs add on their own. Training AUC of the displayed model: {f3(best.train_auc)}.
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-graphite-900/10 bg-ivory/60 p-4">
          <div className="text-sm font-semibold">Calibration on 2026</div>
          <p className="mt-0.5 text-[12px] text-graphite-600">Cell-days grouped into deciles of predicted probability: mean prediction vs observed share with a detection.</p>
          <table className="mt-2 w-full text-left text-[12.5px]">
            <thead className="text-graphite-600"><tr><th className="py-1 pr-2 font-medium">Decile</th><th className="py-1 pr-2 font-medium">Predicted</th><th className="py-1 font-medium">Observed</th></tr></thead>
            <tbody className="font-mono tabular">{best.reliability.map((b: any) => <tr key={b.bin} className="border-t border-graphite-900/8"><td className="py-0.5 pr-2">{b.bin}</td><td className="py-0.5 pr-2">{pct(b.mean_predicted)}</td><td className="py-0.5">{pct(b.observed_rate)}</td></tr>)}</tbody>
          </table>
        </div>
        <div className="rounded-xl border border-graphite-900/10 bg-ivory/60 p-4">
          <div className="text-sm font-semibold">What the model relies on (2026)</div>
          <p className="mt-0.5 text-[12px] text-graphite-600">Drop in AUC when each input is shuffled (permutation importance, 5 repeats).</p>
          <ul className="mt-2 space-y-1">
            {r.permutation_importance_test.slice(0, 10).map((x: any) => (
              <li key={x.feature} className="flex items-center gap-2 text-[12px]">
                <span className="w-44 shrink-0 truncate">{x.label}</span>
                <span className="relative h-2.5 flex-1 overflow-hidden rounded-sm bg-graphite-900/8"><span className="absolute inset-y-0 left-0 rounded-sm bg-graphite-700" style={{ width: `${Math.max(0, x.auc_drop / maxDrop) * 100}%` }} /></span>
                <span className="w-14 text-right font-mono">{x.auc_drop.toFixed(3)}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div className="text-[13.5px]">
        <div className="text-sm font-semibold">Limitations</div>
        <ul className="mt-1 list-disc space-y-1 pl-5">{r.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
        <p className="mt-1 text-[12px] text-graphite-600">{r.selection_rule}</p>
      </div>
    </div>
  )
}
