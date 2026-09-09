import { Marked, Renderer } from 'marked'

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, char => {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]!
  })
}

function safeUrl(value: string, image = false) {
  try {
    const url = new URL(value)
    return ['https:', 'http:', ...(image ? [] : ['mailto:'])].includes(url.protocol)
  } catch {
    return false
  }
}

const renderer = new Renderer()
renderer.html = ({ text }) => escapeHtml(text)
renderer.link = function ({ href, title, tokens }) {
  const text = this.parser.parseInline(tokens)
  if (!safeUrl(href)) return text
  const titleAttribute = title ? ` title="${escapeHtml(title)}"` : ''
  return `<a href="${escapeHtml(href)}"${titleAttribute} target="_blank" rel="noopener noreferrer">${text}</a>`
}
renderer.image = ({ href, text, title }) => {
  if (!safeUrl(href, true)) return escapeHtml(text)
  const titleAttribute = title ? ` title="${escapeHtml(title)}"` : ''
  return `<img src="${escapeHtml(href)}" alt="${escapeHtml(text)}"${titleAttribute} loading="lazy" />`
}

const markdown = new Marked({ renderer, async: false })

export function renderResearchMarkdown(value: string): string {
  return markdown.parse(value, { async: false })
}
