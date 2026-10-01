// D85: the text of a note pinned to a node's card. It is a small subset of HTML, the same one
// clean_body in src/chembook3d/services/notes.py keeps. Notes are shown by building React
// elements from that subset (components/NoteContent.tsx), never by inserting HTML, so nothing
// in a note can run in the page.

import { createContext, useContext } from 'react'
import type { Note } from './api'

/** Where a note's picture comes from: the API in the app, a data URL in the read-only copy. */
export const NoteImageSource = createContext<(id: string) => string | undefined>(
  (id) => `/api/note-images/${id}`,
)

export const useNoteImage = () => useContext(NoteImageSource)

export const ALLOWED: Record<string, string[]> = {
  ...Object.fromEntries(['p', 'div', 'br', 'hr', 'blockquote', 'pre', 'code'].map((t) => [t, []])),
  ...Object.fromEntries(['b', 'strong', 'i', 'em', 'u', 's', 'strike', 'sub', 'sup'].map((t) => [t, []])),
  ...Object.fromEntries(['ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4'].map((t) => [t, []])),
  a: ['href'],
  img: ['data-note-image', 'alt'],
}
export const VOID = new Set(['br', 'hr', 'img'])
export const SKIPPED = new Set([
  'script', 'style', 'template', 'iframe', 'object', 'embed', 'noscript', 'svg', 'math', 'head', 'title',
  'textarea', 'select', 'button',
])
const IMAGE_ID = /^[0-9a-f]{64}$/
const SAFE_LINK = /^(https?:|mailto:)/i

/** A document that loads nothing and runs nothing (DOMParser's documents are inert). */
export function parse(html: string): Document {
  return new DOMParser().parseFromString(html, 'text/html')
}

export function attributes(element: Element, tag: string): [string, string][] {
  const kept: [string, string][] = []
  for (const name of ALLOWED[tag]) {
    const value = element.getAttribute(name)?.trim()
    if (value === undefined) continue
    if (name === 'href' && !SAFE_LINK.test(value)) continue
    if (name === 'data-note-image' && !IMAGE_ID.test(value)) continue
    kept.push([name, value])
  }
  return kept
}

/** Google Docs wraps a whole paste in <b style="font-weight:normal">; that is not bold. */
function notReallyBold(element: Element, tag: string): boolean {
  return (tag === 'b' || tag === 'strong') && /font-weight:\s*(normal|400)/.test(element.getAttribute('style') ?? '')
}

function copyInto(from: ParentNode, to: HTMLElement) {
  for (const child of Array.from(from.childNodes)) {
    if (child.nodeType === 3) {
      to.append(child.textContent ?? '')
      continue
    }
    if (!(child instanceof Element)) continue
    const tag = child.tagName.toLowerCase()
    if (SKIPPED.has(tag)) continue
    if (!(tag in ALLOWED) || notReallyBold(child, tag)) {
      copyInto(child, to)
      continue
    }
    const kept = attributes(child, tag)
    if (tag === 'img' && !kept.some(([name]) => name === 'data-note-image')) continue
    const element = to.ownerDocument.createElement(tag)
    for (const [name, value] of kept) element.setAttribute(name, value)
    if (!VOID.has(tag)) copyInto(child, element)
    to.append(element)
  }
}

/** The note HTML with only the allowed tags and attributes, as the server stores it. */
export function cleanNoteHtml(html: string): string {
  const out = document.createElement('div')
  copyInto(parse(html).body, out)
  return out.innerHTML
}

export function noteImages(html: string): string[] {
  return Array.from(parse(html).querySelectorAll('img[data-note-image]')).map((img) => img.getAttribute('data-note-image')!)
}

export function notePlainText(html: string): string {
  const doc = parse(html.replace(/<(br|\/p|\/div|\/li|\/h[1-4])>/g, ' $&'))
  return (doc.body.textContent ?? '').replace(/\s+/g, ' ').trim()
}

/** The note's title, or else the start of its text, or what it holds. */
export function noteTitle(note: Pick<Note, 'title' | 'body'>): string {
  if (note.title.trim()) return note.title.trim()
  const text = notePlainText(note.body)
  if (text) return text.length > 40 ? `${text.slice(0, 40)}…` : text
  return noteImages(note.body).length ? 'Picture' : 'Empty note'
}

