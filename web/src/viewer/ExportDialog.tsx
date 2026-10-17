import type { ReactNode } from "react";

type ExportDialogProps = {
  open: boolean;
  onClose: () => void;
  /** One line under the title, such as which prediction run the bird's-eye figure will draw. */
  detail?: string;
  children: ReactNode;
};

export default function ExportDialog({ open, onClose, detail, children }: ExportDialogProps) {
  if (!open) {
    return null;
  }
  return (
    <div className="dialog-back" role="presentation" onMouseDown={onClose}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-title"
        aria-describedby={detail ? "export-detail" : undefined}
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            onClose();
          }
        }}
      >
        <div className="dialog-head">
          <h2 id="export-title">Export</h2>
          <button type="button" className="ghost" onClick={onClose}>
            Close
          </button>
        </div>
        {detail ? (
          <p id="export-detail" className="muted dialog-detail">
            {detail}
          </p>
        ) : null}
        {children}
      </div>
    </div>
  );
}
