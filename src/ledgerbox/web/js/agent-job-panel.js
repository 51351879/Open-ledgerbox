// SPDX-License-Identifier: AGPL-3.0-or-later
//
// What one stretch of classification work added up to, and the control that
// asks for another round.
//
// This panel exists because reporting the newest round alone was actively
// misleading: a multi-file import queues one job per file, the last one always
// has the least left to find, and a run that classified 152 of 270 candidates
// across fifteen minutes was shown as "2 submitted". Every number here is about
// the whole stretch, and a stretch that is still moving says so rather than
// leaving a stale figure on screen.

import { button, el } from './api.js';
import { IN_FLIGHT } from './agent-contract.js';

/** Whole minutes and seconds, because a run is minutes long and nobody counts ms. */
function spoken(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  const whole = Math.round(seconds);
  if (whole < 90) return `${whole}s`;
  return `${Math.round(whole / 60)} min`;
}

/** Seconds between two ISO stamps, or null when either is missing or unparsable. */
function secondsBetween(from, to) {
  const start = Date.parse(from);
  const end = Date.parse(to);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  return (end - start) / 1000;
}

export function createJobPanel({ onNeedsClassification, onClassifyNow, now = () => Date.now() }) {
  const node = el('section', 'agent-job');
  node.appendChild(el('p', 'agent-job__label', 'Latest classification'));

  const summary = el('p', 'agent-job__summary', 'No automatic classification run yet.');
  summary.setAttribute('aria-live', 'polite');
  // A bar carries "how far along" at a glance; the text under it carries the
  // numbers the bar cannot honestly show, including what is only an estimate.
  const meter = el('div', 'agent-job__meter');
  meter.setAttribute('role', 'progressbar');
  meter.setAttribute('aria-valuemin', '0');
  meter.setAttribute('aria-valuemax', '100');
  const meterFill = el('div', 'agent-job__meter-fill');
  meter.appendChild(meterFill);
  meter.hidden = true;
  const progress = el('p', 'agent-job__progress');
  const detail = el('p', 'agent-job__detail');
  const needsLink = el('a', 'agent-job__needs');
  needsLink.setAttribute('href', '#transactions');
  needsLink.hidden = true;
  needsLink.addEventListener('click', (event) => {
    if (!needsLink.hidden && onNeedsClassification) {
      event.preventDefault();
      onNeedsClassification();
    }
  });
  const classifyNow = button('btn btn--quiet btn--compact', 'Classify now', () => onClassifyNow());
  classifyNow.disabled = true;

  node.appendChild(summary);
  node.appendChild(meter);
  node.appendChild(progress);
  node.appendChild(detail);
  node.appendChild(needsLink);
  node.appendChild(classifyNow);

  function setMeter(done, total) {
    if (!total || total < 1) {
      meter.hidden = true;
      return;
    }
    const percent = Math.max(0, Math.min(100, Math.round((done / total) * 100)));
    meter.hidden = false;
    meterFill.style.width = `${percent}%`;
    meter.setAttribute('aria-valuenow', String(percent));
    meter.setAttribute('aria-label', `${done} of ${total} candidates classified`);
  }

  function renderMoving(batch) {
    const rounds = batch.job_count === 1 ? 'round' : 'rounds';
    summary.textContent = batch.state === 'queued'
      ? `Classification queued · ${batch.job_count} ${rounds}.`
      : `Classifying now · round ${batch.job_count} · ${batch.submitted_count} submitted so far.`;
    setMeter(batch.submitted_count, batch.candidate_count);

    const elapsed = secondsBetween(batch.started_at, new Date(now()).toISOString());
    const parts = [`Round ${batch.job_count} of at most ${batch.max_rounds}`];
    if (elapsed !== null) {
      parts.push(`running ${spoken(elapsed)}`);
      // The per-round rate is measured, so it is stated. How many rounds are
      // still needed is not knowable -- yield falls off unevenly -- so the
      // remaining time is given as a ceiling and named as one.
      const perRound = elapsed / Math.max(1, batch.job_count);
      const ceiling = spoken(perRound * Math.max(0, batch.max_rounds - batch.job_count));
      if (ceiling) parts.push(`up to ${ceiling} left if it uses every round`);
    }
    progress.textContent = `${parts.join(' · ')}.`;
    detail.textContent = 'This page is watching; it updates itself. You can leave it running.';
  }

  function renderStopped(batch, omitted) {
    const candidate = Math.max(0, Number(batch.candidate_count) || 0);
    const rounds = batch.job_count === 1 ? '1 round' : `${batch.job_count} rounds`;
    // Submitted and applied stay separate: under review-first nothing is
    // applied, and one number standing for both would hide that.
    summary.textContent = batch.state === 'failed'
      ? `Classification failed${batch.error_code ? ` (${batch.error_code})` : ''}.`
      : `Finished · ${candidate} candidates · ${batch.submitted_count} submitted · `
        + `${batch.applied_count} applied · ${omitted} omitted.`;
    setMeter(batch.submitted_count, candidate);
    const ran = secondsBetween(batch.started_at, batch.finished_at);
    progress.textContent = `${rounds}${ran === null ? '' : ` in ${spoken(ran)}`}`
      + (batch.failed_rounds
        ? `, ${batch.failed_rounds} of them returned nothing`
        : '')
      + '.';
    detail.textContent = `Ended ${batch.finished_at || 'unknown'}.`
      + (batch.state !== 'failed' && batch.submitted_count === 0 && candidate > 0
        ? ' The Agent examined every candidate and declined them all under its abstention'
          + ' rules. These need a person: classify a few in Transactions and each answer'
          + ' also claims its identical descriptors.'
        : '')
      + (batch.client_outcome && batch.client_outcome !== 'exited'
        ? ` The client ended early (${batch.client_outcome}), so this is not a considered stopping point.`
        : '')
      + (batch.rounds_capped
        ? ' It stopped at the round limit while still finding work, so asking again may find more.'
        : '');
  }

  /** Render one stretch and return how many transactions are left over now. */
  function render(batch, policy) {
    const moving = batch !== null && batch !== undefined && IN_FLIGHT.has(batch.state);
    const omitted = moving ? 0 : Math.max(0, Number(batch?.omitted_count) || 0);
    if (!batch) {
      summary.textContent = 'No automatic classification run yet.';
      detail.textContent = '';
      progress.textContent = '';
      meter.hidden = true;
    } else if (moving) {
      renderMoving(batch);
    } else {
      renderStopped(batch, omitted);
    }
    classifyNow.disabled = moving || !policy?.enabled || !policy?.selected_client;
    needsLink.textContent = `Needs classification: ${omitted}`;
    needsLink.hidden = omitted === 0;
    return omitted;
  }

  function unavailable() {
    classifyNow.disabled = true;
  }

  return {
    node,
    render,
    unavailable,
    nodes: {
      jobSummary: summary, jobDetail: detail, jobProgress: progress,
      jobMeter: meter, needsLink, classifyNow,
    },
  };
}
