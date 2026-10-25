export function Card({ children, className = '' }) {
  return <div className={`card ${className}`}>{children}</div>
}

export function CardHead({ title, sub, right }) {
  return (
    <div className="card-head">
      <div>
        <h3>{title}</h3>
        {sub && <div className="sub">{sub}</div>}
      </div>
      {right && <div style={{ marginLeft: 'auto' }}>{right}</div>}
    </div>
  )
}

export function MetricCard({ label, value, icon: Icon, trend, valueClass = '' }) {
  return (
    <div className="card card-pad metric">
      <div className="label">
        {Icon && <Icon size={14} />} {label}
      </div>
      <div className={`value ${valueClass}`}>{value}</div>
      {trend && <div className="trend">{trend}</div>}
    </div>
  )
}
