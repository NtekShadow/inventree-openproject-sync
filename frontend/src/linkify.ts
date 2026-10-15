/**
 * Makes OP# work package references clickable anywhere in the InvenTree UI.
 *
 * InvenTree is a React app, so the DOM is never modified (wrapping text in <a>
 * elements would break React's reconciliation). Instead, references are
 * underlined with the CSS Custom Highlight API and a document level click
 * handler opens the work package when a highlighted range is clicked.
 *
 * Installed once per page load by the panel or dashboard item; it then keeps
 * working across client side navigation.
 */

export type LinkifyConfig = {
  op_url: string;
  prefix: string;
  link_template: string;
  linkify: boolean;
};

const HIGHLIGHT_NAME = 'openproject-ref';
const SKIP_TAGS = new Set([
  'A',
  'SCRIPT',
  'STYLE',
  'TEXTAREA',
  'INPUT',
  'CODE',
  'PRE'
]);
const ID_PATTERN = '(?:[A-Za-z][A-Za-z0-9_]*-\\d+|\\d+)';

type Match = { range: Range; url: string };

declare global {
  interface Window {
    __openprojectLinkify?: { config: LinkifyConfig };
  }
}

function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** 'el-05' -> 'EL-5', '0012' -> '12' (same as refs.normalize_key in Python) */
export function normalizeKey(raw: string): string {
  if (/^\d+$/.test(raw)) {
    return String(Number.parseInt(raw, 10));
  }
  const idx = raw.lastIndexOf('-');
  return `${raw.slice(0, idx).toUpperCase()}-${Number.parseInt(raw.slice(idx + 1), 10)}`;
}

export function workPackageUrl(config: LinkifyConfig, key: string): string {
  return config.link_template
    .replace('{base}', config.op_url.replace(/\/+$/, ''))
    .replace('{id}', key);
}

export function refRegex(prefix: string): RegExp {
  return new RegExp(
    `(?<![\\w/])${escapeRegex(prefix)}(${ID_PATTERN})(?![\\w-])`,
    'gi'
  );
}

function isSkipped(node: Node): boolean {
  for (let el = node.parentElement; el; el = el.parentElement) {
    if (SKIP_TAGS.has(el.tagName) || el.isContentEditable) {
      return true;
    }
  }
  return false;
}

function scan(root: HTMLElement, config: LinkifyConfig): Match[] {
  const regex = refRegex(config.prefix);
  const matches: Match[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.nodeValue ?? '';
    if (
      !text.toUpperCase().includes(config.prefix.toUpperCase()) ||
      isSkipped(node)
    ) {
      continue;
    }
    regex.lastIndex = 0;
    for (let m = regex.exec(text); m; m = regex.exec(text)) {
      const range = document.createRange();
      range.setStart(node, m.index);
      range.setEnd(node, m.index + m[0].length);
      matches.push({ range, url: workPackageUrl(config, normalizeKey(m[1])) });
    }
  }
  return matches;
}

function caretAt(x: number, y: number): { node: Node; offset: number } | null {
  const doc = document as any;
  if (doc.caretPositionFromPoint) {
    const pos = doc.caretPositionFromPoint(x, y);
    return pos ? { node: pos.offsetNode, offset: pos.offset } : null;
  }
  if (doc.caretRangeFromPoint) {
    const range: Range | null = doc.caretRangeFromPoint(x, y);
    return range
      ? { node: range.startContainer, offset: range.startOffset }
      : null;
  }
  return null;
}

/** Install the highlighter once; later calls only update the configuration. */
export function installLinkify(config: LinkifyConfig | undefined): void {
  if (
    !config?.linkify ||
    !config.op_url ||
    typeof CSS === 'undefined' ||
    !('highlights' in CSS)
  ) {
    return;
  }
  if (window.__openprojectLinkify) {
    window.__openprojectLinkify.config = config;
    return;
  }
  const state = { config };
  window.__openprojectLinkify = state;

  const highlight = new Highlight();
  CSS.highlights.set(HIGHLIGHT_NAME, highlight);

  const style = document.createElement('style');
  style.textContent = `::highlight(${HIGHLIGHT_NAME}) { color: var(--mantine-color-anchor, #228be6); text-decoration: underline; }`;
  document.head.appendChild(style);

  let matches: Match[] = [];
  let timer: number | undefined;

  const refresh = () => {
    timer = undefined;
    matches = scan(document.body, state.config);
    highlight.clear();
    for (const m of matches) {
      highlight.add(m.range);
    }
  };

  const hit = (event: MouseEvent): Match | undefined => {
    if (!matches.length) {
      return undefined;
    }
    const caret = caretAt(event.clientX, event.clientY);
    if (!caret) {
      return undefined;
    }
    return matches.find(
      (m) =>
        m.range.startContainer === caret.node &&
        caret.offset >= m.range.startOffset &&
        caret.offset <= m.range.endOffset
    );
  };

  new MutationObserver(() => {
    if (timer === undefined) {
      timer = window.setTimeout(refresh, 250);
    }
  }).observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true
  });

  document.addEventListener(
    'click',
    (event) => {
      if (event.button !== 0 || !window.getSelection()?.isCollapsed) {
        return;
      }
      const match = hit(event);
      if (match) {
        event.preventDefault();
        event.stopPropagation();
        window.open(match.url, '_blank', 'noopener');
      }
    },
    true
  );

  let pointer = false;
  document.addEventListener('mousemove', (event) => {
    const over = !!hit(event);
    if (over !== pointer) {
      pointer = over;
      document.documentElement.style.cursor = over ? 'pointer' : '';
    }
  });

  refresh();
}
