import { useEffect, useId, useRef } from 'react';
import type { ComponentPropsWithRef, ComponentPropsWithoutRef, HTMLAttributes, ReactNode } from 'react';
import './Primitives.css';

export type ButtonProps = ComponentPropsWithRef<'button'> & {
  variant?: 'primary' | 'secondary';
};

export function Button({ variant = 'secondary', className = '', type = 'button', ...props }: ButtonProps) {
  return <button {...props} type={type} className={`btn btn--${variant} ${className}`} />;
}

export type PanelProps = Omit<HTMLAttributes<HTMLElement>, 'title'> & {
  title?: ReactNode;
  children: ReactNode;
};

export function Panel({ title, children, className = '', ...props }: PanelProps) {
  const titleId = useId();
  return (
    <section {...props} aria-labelledby={props['aria-labelledby'] ?? (title ? titleId : undefined)} className={`ui-panel ${className}`}>
      {title ? <h2 id={titleId}>{title}</h2> : null}
      {children}
    </section>
  );
}

export type FieldProps = ComponentPropsWithRef<'input'> & {
  label: string;
  hint?: ReactNode;
  error?: string;
};

export function Field({ label, hint, error, id, ...props }: FieldProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;
  const describedBy = [props['aria-describedby'], hint ? hintId : undefined, error ? errorId : undefined].filter(Boolean).join(' ') || undefined;
  return (
    <div className="ui-field">
      <label htmlFor={inputId}>{label}</label>
      <input {...props} id={inputId} aria-describedby={describedBy} aria-invalid={error ? true : props['aria-invalid']} />
      {hint ? <p id={hintId}>{hint}</p> : null}
      {error ? <p id={errorId} role="alert">{error}</p> : null}
    </div>
  );
}

export interface SegmentedOption<Value extends string> {
  value: Value;
  label: string;
  disabled?: boolean;
}

export interface SegmentedControlProps<Value extends string> {
  label: string;
  value: Value;
  options: readonly SegmentedOption<Value>[];
  onChange: (value: Value) => void;
  name?: string;
  describedBy?: string;
  disabled?: boolean;
}

export function SegmentedControl<Value extends string>({ label, value, options, onChange, name, describedBy, disabled }: SegmentedControlProps<Value>) {
  const generatedName = useId();
  return (
    <fieldset className="ui-segmented" aria-describedby={describedBy} disabled={disabled}>
      <legend>{label}</legend>
      <div className="ui-segmented__options">
        {options.map((option) => (
          <label key={option.value}>
            <input
              type="radio"
              name={name ?? generatedName}
              value={option.value}
              checked={value === option.value}
              disabled={option.disabled}
              onChange={() => onChange(option.value)}
            />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export interface ProgressBarProps {
  label: string;
  value?: number;
  max?: number;
  valueText?: string;
}

export function ProgressBar({ label, value, max = 100, valueText }: ProgressBarProps) {
  const id = useId();
  const limit = Number.isFinite(max) && max > 0 ? max : 100;
  const current = value !== undefined && Number.isFinite(value) ? Math.min(limit, Math.max(0, value)) : undefined;
  const description = valueText ?? (current === undefined ? 'In progress' : `${Math.round(current / limit * 100)}%`);
  return (
    <div className="ui-progress">
      <div className="ui-progress__label">
        <label id={`${id}-label`} htmlFor={id}>{label}</label>
        <span aria-hidden="true">{description}</span>
      </div>
      <progress id={id} aria-labelledby={`${id}-label`} max={limit} value={current} aria-valuetext={description}>{description}</progress>
    </div>
  );
}

export type StatusLineProps = HTMLAttributes<HTMLParagraphElement> & {
  kind?: 'status' | 'error';
};

export function StatusLine({ kind = 'status', children, className = '', ...props }: StatusLineProps) {
  return (
    <p {...props} className={`ui-status ${className}`} role={kind === 'error' ? 'alert' : 'status'} aria-atomic="true">
      {kind === 'error' ? <strong>Error: </strong> : null}{children}
    </p>
  );
}

export type DialogProps = Omit<ComponentPropsWithoutRef<'dialog'>, 'open' | 'onClose' | 'onCancel' | 'title'> & {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
};

export function Dialog({ open, onClose, title, description, children, className = '', ...props }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      {...props}
      ref={ref}
      className={`ui-dialog ${className}`}
      aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined}
      onClose={() => { if (open) onClose(); }}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <header className="ui-dialog__header">
        <h2 id={titleId}>{title}</h2>
        <Button onClick={onClose} aria-label="Close dialog">Close dialog</Button>
      </header>
      {description ? <p id={descriptionId}>{description}</p> : null}
      {children}
    </dialog>
  );
}

export type ListRowProps = HTMLAttributes<HTMLLIElement> & {
  label: ReactNode;
  description?: ReactNode;
  trailing?: ReactNode;
};

export function ListRow({ label, description, trailing, className = '', children, ...props }: ListRowProps) {
  return (
    <li {...props} className={`ui-list-row ${className}`}>
      <div className="ui-list-row__content">
        <div>{label}</div>
        {description ? <p>{description}</p> : null}
        {children}
      </div>
      {trailing ? <div className="ui-list-row__trailing">{trailing}</div> : null}
    </li>
  );
}

export interface EmptyStateProps {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}

export function EmptyState({ title, children, action }: EmptyStateProps) {
  const id = useId();
  return (
    <section className="ui-empty" aria-labelledby={id}>
      <h2 id={id}>{title}</h2>
      <div>{children}</div>
      {action ? <div className="ui-empty__action">{action}</div> : null}
    </section>
  );
}
