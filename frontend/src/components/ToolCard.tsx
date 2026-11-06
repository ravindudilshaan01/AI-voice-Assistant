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

  if (tool.name === 'get_weather' && tool.state === 'done') {
    return <aside className="tool-card weather-card" aria-label="Weather result">
      <div className="tool-card-icon" aria-hidden="true">☁</div>
      <div className="tool-card-main">
        <span className="tool-kicker">Current weather</span>
        <strong>{text(result.location) || 'Weather result'}</strong>
        <span>{text(result.condition)} · Feels like {text(result.feels_like)}{text(result.temperature_unit)}</span>
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
        {source && <a href={source} target="_blank" rel="noreferrer noopener">Open source</a>}
      </div>
    </aside>
  }

  if (tool.name === 'schedule_event' && tool.state === 'done') {
    return <aside className="tool-card" aria-label="Saved reminder">
      <div className="tool-card-icon" aria-hidden="true">✓</div>
      <div className="tool-card-main">
        <span className="tool-kicker">Saved in this session</span>
        <strong>{text(result.title)}</strong>
        <span>{text(result.when)}</span>
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
