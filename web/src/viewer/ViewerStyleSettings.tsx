// SPDX-License-Identifier: Apache-2.0
import { SettingRow } from "../desktop/components/SettingRow";
import { StylePreview } from "./StylePreview";
import {
  effectiveCinemaDrift,
  useViewerStyle,
  VIEWER_STYLES,
  VIEWER_STYLE_TOKENS,
  type ViewerStyle,
} from "./viewerStyle";

/** Settings → Viewer → Style. Four live cards, and Cinema's drift when it is selected. */
export function ViewerStyleSettings() {
  const { style, setStyle, drift, setDrift, reducedMotion } = useViewerStyle();
  const driftOn = effectiveCinemaDrift(drift, reducedMotion);
  return (
    <div className="viewer-style-setting" style={{ borderTop: "1px solid var(--line-subtle)", padding: "8px 0 4px" }}>
      <div style={{ padding: "0 0 6px", fontSize: "13px", fontWeight: 500, color: "var(--text)" }}>Style</div>
      <div role="radiogroup" aria-label="Style" className="viewer-style-grid">
        {VIEWER_STYLES.map((value) => {
          const tokens = VIEWER_STYLE_TOKENS[value];
          const selected = style === value;
          return (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={selected}
              className="viewer-style-card"
              data-style={value}
              data-selected={selected ? "true" : "false"}
              onClick={() => setStyle(value as ViewerStyle)}
            >
              <StylePreview style={value} drift={driftOn} reducedMotion={reducedMotion} />
              <span className="viewer-style-name">{tokens.label}</span>
              <span className="viewer-style-blurb">{tokens.blurb}</span>
            </button>
          );
        })}
      </div>
      {style === "cinema" ? (
        <SettingRow
          checked={driftOn}
          disabled={reducedMotion}
          title="Cinema camera drift"
          description={
            reducedMotion
              ? "Off while the system asks for reduced motion."
              : "The camera turns slowly while the scene is playing."
          }
          onChange={(checked) => setDrift(checked)}
        />
      ) : null}
    </div>
  );
}
