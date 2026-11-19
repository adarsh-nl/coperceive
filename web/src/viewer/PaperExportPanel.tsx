import { PAPER_METRICS, type PaperOptions, toggleMetric, type PaperMetricId } from "./paperExport";

type PaperExportPanelProps = {
  options: PaperOptions;
  disabled: boolean;
  onChange: (options: PaperOptions) => void;
  onExport: () => void;
};

export default function PaperExportPanel({
  options,
  disabled,
  onChange,
  onExport,
}: PaperExportPanelProps) {
  return (
    <fieldset className="paper-export" disabled={disabled}>
      <legend>Paper</legend>
      <div className="paper-options">
        {PAPER_METRICS.map((metric) => (
          <label key={metric.id} htmlFor={`paper-metric-${metric.id}`}>
            <input
              id={`paper-metric-${metric.id}`}
              type="checkbox"
              checked={options.metrics.includes(metric.id)}
              onChange={() => onChange(toggleMetric(options, metric.id as PaperMetricId))}
            />
            {metric.label}
          </label>
        ))}
        <label htmlFor="paper-counts">
          <input
            id="paper-counts"
            type="checkbox"
            checked={options.includeCounts}
            onChange={() => onChange({ ...options, includeCounts: !options.includeCounts })}
          />
          Object counts
        </label>
        <label htmlFor="paper-bold">
          <input
            id="paper-bold"
            type="checkbox"
            checked={options.boldBest}
            onChange={() => onChange({ ...options, boldBest: !options.boldBest })}
          />
          Bold the best value
        </label>
        <label htmlFor="paper-decimals">
          Decimals
          <input
            id="paper-decimals"
            type="number"
            min={0}
            max={6}
            value={options.decimals}
            onChange={(event) => {
              const next = Number(event.target.value);
              if (Number.isInteger(next)) {
                onChange({ ...options, decimals: Math.min(6, Math.max(0, next)) });
              }
            }}
          />
        </label>
        <label htmlFor="paper-qualitative">
          <input
            id="paper-qualitative"
            type="checkbox"
            checked={options.qualitative}
            onChange={() => onChange({ ...options, qualitative: !options.qualitative })}
          />
          Qualitative frames
        </label>
        <label htmlFor="paper-griffin">
          <input
            id="paper-griffin"
            type="checkbox"
            checked={options.allowGriffin}
            onChange={() => onChange({ ...options, allowGriffin: !options.allowGriffin })}
          />
          Allow Griffin
        </label>
        <label htmlFor="paper-carla">
          <input
            id="paper-carla"
            type="checkbox"
            checked={options.allowCarla}
            onChange={() => onChange({ ...options, allowCarla: !options.allowCarla })}
          />
          Allow CARLA
        </label>
      </div>
      <p className="paper-note">
        Fixtures are included. Griffin and CARLA stay off until you allow them. The dataset
        licence decision is still open.
      </p>
      <button id="export-paper" type="button" className="ghost" onClick={onExport} disabled={disabled}>
        Export for paper
      </button>
    </fieldset>
  );
}
