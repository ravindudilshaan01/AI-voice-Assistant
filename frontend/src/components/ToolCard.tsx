export type ToolView = {
  id: string
  name: string
  label: string
  state: 'running' | 'done' | 'error'
  result?: Record<string, unknown>
  error?: string
}

type Props = { tool: ToolView }

const text = (value: unknown) => typeof value === 'string' || typeof value === 'number' ? String(value) : ''
const safeHttpUrl = (value: string) => {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : ''
  } catch { return '' }
}

export function ToolCard({ tool }: Props) {
  const result = tool.result || {}
  const source = safeHttpUrl(text(result.source))
  const sources = Array.isArray(result.sources) ? result.sources.flatMap((item) => {
    if (typeof item !== 'object' || item === null) return []
    const record = item as Record<string, unknown>
    const url = safeHttpUrl(text(record.url))
    return url ? [{ title: text(record.title) || 'Open source', url }] : []
  }).slice(0, 3) : []

  if (tool.name === 'get_weather' && tool.state === 'done') {
    return <aside className="tool-card weather-card" aria-label="Weather result">
      <div className="tool-card-icon" aria-hidden="true">☁</div>
      <div className="tool-card-main">
        <span className="tool-kicker">Current weather</span>
        <strong>{text(result.location) || 'Weather result'}</strong>
        <span>{text(result.condition)} · Feels like {text(result.feels_like)}{text(result.temperature_unit)}</span>
        <span>Humidity {text(result.relative_humidity)}{text(result.humidity_unit)} · Wind {text(result.wind_speed)} {text(result.wind_speed_unit)}</span>
        <small>Observed {text(result.observed_at)} · {text(result.source)}</small>
        {text(result.today_rain_chance) && <span>Today: {text(result.today_rain_chance)}{text(result.today_rain_chance_unit)} rain chance · High {text(result.today_high)}{text(result.today_temperature_unit)}</span>}
      </div>
      <div className="weather-temperature">{text(result.temperature)}{text(result.temperature_unit)}</div>
    </aside>
  }

  if (tool.name === 'web_search' && tool.state === 'done') {
    return <aside className="tool-card search-card" aria-label="Search source">
      <div className="tool-card-icon" aria-hidden="true">↗</div>
      <div className="tool-card-main">
        <span className="tool-kicker">Web source</span>
        <strong>{text(result.query) || 'Search result'}</strong>
        {text(result.answer) && <span>{text(result.answer)}</span>}
        {sources.length > 0
          ? <div className="search-sources">{sources.map((item) => <a key={item.url} href={item.url} target="_blank" rel="noreferrer noopener">{item.title}</a>)}</div>
          : source && <a href={source} target="_blank" rel="noreferrer noopener">Open source</a>}
      </div>
    </aside>
  }

  if (tool.name === 'schedule_event' && tool.state === 'done') {
    return <aside className="tool-card" aria-label="Saved reminder">
      <div className="tool-card-icon" aria-hidden="true">✓</div>
      <div className="tool-card-main">
        <span className="tool-kicker">Spoken reminder scheduled</span>
        <strong>{text(result.title)}</strong>
        <span>{text(result.display_time) || text(result.when)}</span>
        <small>Keep this page connected so the reminder can speak.</small>
      </div>
    </aside>
  }

  if (tool.name === 'calculate' && tool.state === 'done') {
    return <aside className="tool-card" aria-label="Calculation result">
      <div className="tool-card-icon" aria-hidden="true">=</div>
      <div className="tool-card-main">
        <span className="tool-kicker">Calculation</span>
        <strong>{text(result.expression)} = {text(result.result)}</strong>
      </div>
    </aside>
  }

  return <aside className={`tool-card ${tool.state}`} aria-label={tool.label}>
    <div className="tool-card-icon" aria-hidden="true">{tool.state === 'running' ? '…' : tool.state === 'error' ? '!' : '✓'}</div>
    <div className="tool-card-main">
      <span className="tool-kicker">{tool.state === 'running' ? 'Working' : tool.state === 'error' ? 'Tool unavailable' : 'Completed'}</span>
      <strong>{tool.label}</strong>
      {tool.error && <span>{tool.error}</span>}
    </div>
  </aside>
}
