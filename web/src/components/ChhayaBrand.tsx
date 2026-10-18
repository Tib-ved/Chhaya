type ChhayaBrandProps = {
  variant?: 'header' | 'lockup' | 'mark'
  className?: string
}

export function ChhayaBrand({ variant = 'header', className = '' }: ChhayaBrandProps) {
  if (variant === 'lockup') return <img
    className={`chhaya-brand-lockup ${className}`}
    src="/chhaya-logo.png"
    alt="Chhaya — A call that brings you shade"
    width="1024"
    height="375"
    decoding="async"
  />

  return <span className={`chhaya-brand chhaya-brand--${variant} ${className}`}>
    <img src="/chhaya-mark.png" alt="" aria-hidden="true" width="256" height="256" decoding="async" />
    {variant === 'header' && <span className="chhaya-brand-type"><strong>Chhaya</strong><small>A call that brings you shade</small></span>}
  </span>
}
