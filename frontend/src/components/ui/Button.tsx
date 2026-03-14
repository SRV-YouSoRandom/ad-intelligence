import React from 'react';

type ButtonVariant = 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger';
type ButtonSize = 'sm' | 'md' | 'lg';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  isLoading?: boolean;
  icon?: React.ReactNode;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className = '', variant = 'primary', size = 'md', isLoading, icon, children, disabled, ...props }, ref) => {
    return (
      <button
        ref={ref}
        disabled={isLoading || disabled}
        className={`btn btn-${variant} btn-${size} ${className}`}
        {...props}
      >
        {isLoading ? (
          <span className="spinner"></span>
        ) : icon ? (
          <span className="btn-icon">{icon}</span>
        ) : null}
        {children && <span className="btn-text">{children}</span>}
      </button>
    );
  }
);

Button.displayName = 'Button';
