import type { ReactNode } from "react";

type ExportDialogProps = {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
};

export default function ExportDialog({ open, onClose, children }: ExportDialogProps) {
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
        {children}
      </div>
    </div>
  );
}
