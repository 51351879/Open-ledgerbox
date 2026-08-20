// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Drop zone and result cards.
//
// Files are posted one at a time and in order. The server serialises writes
// anyway, so parallel requests buy nothing — and when thirteen statements go up
// at once, which one produced which refusal stops being answerable.
//
// A `needs_review` card is not an error. It is the gate doing its job: the
// bytes are archived, nothing was booked, and the reason is printed in full.

import { ApiError, clear, el, uploadStatement } from './api.js';
import { t } from './i18n.js';
import { reviewItemNode } from './review.js';

// A nested table, so `localized()` is no use here: it looks up strings one
// level down and would half-translate this one, leaving a `tone` that is a
// class name looking like something it could touch. The four labels are
// looked up where they are read, and the `tone`s are never looked up at all.
const STATUS_META = {
  imported: { tone: 'ok', label: 'Imported' },
  duplicate: { tone: 'neutral', label: 'Already imported' },
  needs_review: { tone: 'warn', label: 'Needs review' },
  failed: { tone: 'fail', label: 'Could not read' },
};

function factsNode(pairs) {
  const list = el('dl', 'facts');
  for (const [label, value, tone] of pairs) {
    list.appendChild(el('dt', 'facts__key', label));
    list.appendChild(el('dd', tone ? `facts__value num ${tone}` : 'facts__value num', value));
  }
  return list;
}

function cardHead(name, badgeClass, badgeLabel) {
  const head = el('header', 'card__head');
  // The filename came from the browser and is echoed by the server as data.
  head.appendChild(el('span', 'card__name', name));
  head.appendChild(el('span', badgeClass, badgeLabel));
  return head;
}

function importedBody(card, result) {
  const pairs = [[t('Booked'), t('{count} transaction(s)', { count: result.booked })]];
  pairs.push([t('Month'), result.statement_month || t('not stated')]);
  if (result.skipped_duplicates > 0) {
    pairs.push([
      t('Skipped as duplicates'),
      t('{count} transaction(s)', { count: result.skipped_duplicates }),
    ]);
  }
  if (result.verdict) {
    // Shown even on success, and marked when it is not a clean `ok`: an import
    // whose verdict is UNVERIFIED is exactly the thing this project must not
    // round off to "fine" because the card around it is green.
    // The verdict itself is a wire value and is shown exactly as it arrived.
    pairs.push([t('Verdict'), result.verdict, result.verdict === 'ok' ? '' : 'flag-warn']);
  }
  card.appendChild(factsNode(pairs));
}

function needsReviewBody(card, result) {
  card.appendChild(
    el(
      'p',
      'card__note',
      t(
        'The file is archived and nothing was booked. Every reason is below, and each '
          + 'one is waiting in the review queue.',
      ),
    ),
  );
  const items = result.review || [];
  if (items.length === 0) {
    card.appendChild(el('p', 'muted', t('No detail was returned with this refusal.')));
    return;
  }
  const holder = el('div', 'card__review');
  for (const item of items) {
    // No buttons here: the queue below is where an item is acted on, and one
    // item rendered twice with two live Resolve buttons is a trap.
    holder.appendChild(reviewItemNode(item));
  }
  card.appendChild(holder);
}

function renderResult(card, fallbackName, result) {
  const meta = STATUS_META[result.status];
  const tone = meta ? meta.tone : 'neutral';
  // A status this page does not know is shown as it arrived: it is a wire
  // value, and inventing a reading for it is the guess this project refuses.
  const label = meta ? t(meta.label) : result.status;
  card.className = `card card--${tone}`;
  clear(card);
  card.appendChild(cardHead(result.filename || fallbackName, `badge badge--${tone}`, label));
  card.appendChild(el('p', 'card__summary', result.summary || ''));

  if (result.status === 'imported') {
    importedBody(card, result);
  } else if (result.status === 'duplicate') {
    card.appendChild(
      el(
        'p',
        'card__note',
        t(
          'These exact bytes were already archived, so there was nothing to do. '
            + 'Re-uploading a statement is always safe.',
        ),
      ),
    );
  } else if (result.status === 'needs_review') {
    needsReviewBody(card, result);
  } else if (result.status === 'failed') {
    card.appendChild(
      el('p', 'card__error', result.error || t('The file could not be read at all.')),
    );
  }
}

function renderRejection(card, name, error) {
  card.className = 'card card--fail';
  clear(card);
  const label = error instanceof ApiError && error.status === 0
    ? t('No answer')
    : t('Rejected');
  card.appendChild(cardHead(name, 'badge badge--fail', label));
  card.appendChild(el('p', 'card__error', error.message || t('The upload was refused.')));
  if (error instanceof ApiError && error.status > 0) {
    card.appendChild(
      el('p', 'card__note muted', t('Server status {status}.', { status: error.status })),
    );
  }
}

/**
 * Wires the page-wide drop target, the file picker, and the result list.
 *
 * `onSettled` fires once a batch has finished, successfully or not, so the
 * queue and the health strip can re-read themselves from the server.
 * `onFiles` fires as a batch starts, so the panel the cards land in can be
 * opened before the first one is drawn.
 */
export function createUploader(options) {
  const results = options.results;
  // The uploader's own furniture, built by the uploader. It is shown only while
  // a drag is over the page, so it has no reason to sit in the document a
  // reader can view-source before anything is dragged anywhere.
  const overlay = el('div', 'drop-overlay');
  overlay.id = 'drop-overlay';
  overlay.hidden = true;
  overlay.appendChild(el('p', 'drop-overlay__text', t('Release to upload')));
  document.body.insertBefore(overlay, document.body.firstChild);
  const fileInput = options.fileInput;
  const onSettled = options.onSettled;

  // Every file appends to this chain, so a second drop that lands mid-upload
  // queues behind the first rather than racing it.
  let chain = Promise.resolve();
  let dragDepth = 0;

  async function sendOne(file) {
    const card = el('article', 'card card--pending');
    card.appendChild(cardHead(file.name, 'badge badge--pending', t('Uploading')));
    card.appendChild(el('p', 'card__summary', t('Reconciling before anything is booked…')));
    results.prepend(card);
    try {
      const result = await uploadStatement(file);
      renderResult(card, file.name, result);
    } catch (error) {
      renderRejection(card, file.name, error);
    }
  }

  function enqueue(fileList) {
    const files = Array.from(fileList || []);
    if (files.length === 0) {
      return;
    }
    // Fired before the first request, not after the batch: the result cards go
    // into a panel that is a disclosure now, and a card rendered inside a closed
    // one is a card nobody sees. Drag and drop is wired on `document` below and
    // never consults that disclosure, so this only reveals what was going to
    // happen anyway — it is not a precondition of an upload.
    if (options.onFiles) {
      options.onFiles(files.length);
    }
    for (const file of files) {
      chain = chain.then(() => sendOne(file));
    }
    chain = chain.then(() => {
      if (onSettled) {
        onSettled();
      }
    });
  }

  function showOverlay(visible) {
    if (overlay) {
      overlay.hidden = !visible;
    }
  }

  function carriesFiles(event) {
    const types = event.dataTransfer ? event.dataTransfer.types : null;
    return Boolean(types) && Array.prototype.indexOf.call(types, 'Files') !== -1;
  }

  document.addEventListener('dragenter', (event) => {
    if (!carriesFiles(event)) {
      return;
    }
    event.preventDefault();
    dragDepth += 1;
    showOverlay(true);
  });

  // Without preventDefault on dragover the browser navigates to the file and
  // the page is gone.
  document.addEventListener('dragover', (event) => {
    if (carriesFiles(event)) {
      event.preventDefault();
    }
  });

  document.addEventListener('dragleave', () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) {
      showOverlay(false);
    }
  });

  document.addEventListener('drop', (event) => {
    if (!carriesFiles(event)) {
      return;
    }
    event.preventDefault();
    dragDepth = 0;
    showOverlay(false);
    enqueue(event.dataTransfer.files);
  });

  if (fileInput) {
    fileInput.addEventListener('change', () => {
      enqueue(fileInput.files);
      // Cleared so that choosing the same file twice fires `change` twice.
      fileInput.value = '';
    });
  }

  if (options.chooseButton && fileInput) {
    options.chooseButton.addEventListener('click', () => fileInput.click());
  }

  if (options.clearButton) {
    options.clearButton.addEventListener('click', () => clear(results));
  }

  return { enqueue };
}
