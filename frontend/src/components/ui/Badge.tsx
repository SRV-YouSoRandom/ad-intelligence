import React from 'react';

export type BadgeVariant = 'success' | 'warning' | 'error' | 'info' | 'neutral';

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  icon?: React.ReactNode;
}

export const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className = '', variant = 'neutral', icon, children, ...props }, ref) => {
    return (
      <span
        ref={ref}
        className={`badge badge-${variant} ${className}`}
        {...props}
      >
        {icon && <span className="badge-icon">{icon}</span>}
        {children}
      </span>
    );
  }
);

Badge.displayName = 'Badge';
