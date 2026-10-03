import assert from 'node:assert/strict';
import test from 'node:test';
import { SubmissionBarrier, prepareAppClaim } from '../src/submission-barrier.ts';
import { consumeAppAction } from '../src/app-actions.ts';
import { FrontendReporter } from '../src/frontend-observation.ts';

const tick = () => new Promise(resolve => setTimeout(resolve, 35));
const scope = 'owner/session/generation/client';
const action = { id: 'action', session_id: 'session', action: 'open', status: 'pending', target_client_id: 'client', expected_revision: 1, payload: { target: 'history' } };

test('the synchronous lock rejects same-tick duplicate sends until the final render commits', () => {
  const barrier = new SubmissionBarrier(), first = barrier.begin(scope);
  assert.ok(first);
  assert.equal(barrier.begin(scope), null);
  const revision = barrier.finish(first);
  assert.equal(barrier.ready(scope), false);
  assert.equal(barrier.begin(scope), null);
  barrier.commit(revision - 1);
  assert.equal(barrier.ready(scope), false, 'a render begun before draft cleanup is not sufficient');
  barrier.commit(revision);
  assert.equal(barrier.ready(scope), true);
  assert.ok(barrier.begin(scope));
});

test('assigning the first conversation moves the same submit instead of leaving a gap for duplicates', () => {
  const barrier = new SubmissionBarrier(), first = barrier.begin('new-conversation');
  barrier.move(first, scope);
  assert.equal(barrier.begin(scope), null);
  assert.equal(barrier.ready(scope), false);
  barrier.commit(barrier.finish(first));
  assert.equal(barrier.ready(scope), true);
});

test('a new scope can submit while the old request hangs and its late finally cannot release the new lock', () => {
  const barrier = new SubmissionBarrier(), old = barrier.begin('old-scope');
  const next = barrier.begin('new-scope');
  assert.ok(next);
  barrier.move(old, 'new-scope');
  assert.equal(barrier.finish(old), null);
  barrier.commit(0);
  assert.equal(barrier.ready('new-scope'), false);
  assert.equal(barrier.begin('new-scope'), null);
  barrier.commit(barrier.finish(next));
  assert.equal(barrier.ready('new-scope'), true);
});

test('an old completion commit cannot release a later submission awaiting its own commit', () => {
  const barrier = new SubmissionBarrier(), old = barrier.begin('old');
  const oldRevision = barrier.finish(old);
  const next = barrier.begin('new'), nextRevision = barrier.finish(next);
  barrier.commit(oldRevision);
  assert.equal(barrier.ready('new'), false);
  barrier.commit(nextRevision);
  assert.equal(barrier.ready('new'), true);
});

test('an action arriving before the send response is claimed only after cleared drafts commit and are reported', async () => {
  const barrier = new SubmissionBarrier(), submitting = barrier.begin(scope);
  let hasDraft = true, reports = 0, claims = 0, effects = 0, observedRevision = 0;
  const reporter = new FrontendReporter('client', () => ({ session_id: 'session', foreground: true, has_unsent_changes: hasDraft }), async observation => {
    reports++; observedRevision = observation.revision;
  });
  // The run produced its action from the still-populated composer observation.
  await reporter.report(); reports = 0;
  const executing = (async () => {
    if (!await prepareAppClaim({ barrier, scope, current: () => true, report: () => reporter.report(true) })) return false;
    return consumeAppAction(action, 'client', async (path) => {
      if (path.endsWith('/claim')) {
        claims++; assert.equal(hasDraft, false); assert.equal(observedRevision, 2);
        // Server's narrow source-only rebase is returned by the claim receipt.
        return { accepted: true, request: { ...action, expected_revision: observedRevision } };
      }
      return { completed: true };
    }, async claimed => {
      assert.equal(claimed.expected_revision, reporter.snapshot().revision);
      assert.equal(hasDraft, false); effects++;
      return { status: 'applied', observed_revision: observedRevision };
    }, () => true);
  })();
  await tick(); assert.equal(reports, 0); assert.equal(claims, 0);
  const revision = barrier.finish(submitting);
  await tick(); assert.equal(reports, 0); assert.equal(claims, 0);
  hasDraft = false; barrier.commit(revision);
  assert.equal(await executing, true);
  assert.equal(reports, 1); assert.equal(claims, 1); assert.equal(effects, 1);
  reporter.stop();
});

test('a new submit starting during the report forces another wait and report before claim readiness', async () => {
  const barrier = new SubmissionBarrier(); let reports = 0, release;
  const pending = prepareAppClaim({ barrier, scope, current: () => true, report: () => {
    reports++;
    return reports === 1 ? new Promise(resolve => { release = resolve; }) : Promise.resolve({ revision: 2 });
  } });
  const ticket = barrier.begin(scope);
  release({ revision: 1 });
  await tick(); assert.equal(reports, 1);
  barrier.commit(barrier.finish(ticket));
  assert.equal(await pending, true);
  assert.equal(reports, 2);
});

test('navigation or identity loss stops a waiting action without a report, claim, or effect', async () => {
  const barrier = new SubmissionBarrier(); barrier.begin(scope);
  let current = true, reports = 0;
  const pending = prepareAppClaim({ barrier, scope, current: () => current, report: async () => { reports++; return {}; } });
  current = false;
  assert.equal(await pending, false);
  assert.equal(reports, 0);
});

test('a hung submit leaves the action unclaimed after a bounded wait and a later delivery may proceed once', async () => {
  const barrier = new SubmissionBarrier(), ticket = barrier.begin(scope); let reports = 0;
  const options = { barrier, scope, current: () => true, report: async () => { reports++; return {}; }, timeoutMs: 10 };
  assert.equal(await prepareAppClaim(options), false);
  assert.equal(reports, 0);
  barrier.commit(barrier.finish(ticket));
  assert.equal(await prepareAppClaim(options), true);
  await tick();
  assert.equal(reports, 1, 'the timed-out waiter cannot wake and send an extra report');
});

test('a report that outlives the deadline or changes identity never authorizes a late claim', async () => {
  const barrier = new SubmissionBarrier(); let release;
  const pending = prepareAppClaim({ barrier, scope, current: () => true, timeoutMs: 10, report: () => new Promise(resolve => { release = resolve; }) });
  assert.equal(await pending, false);
  release({ revision: 1 }); await tick();
  let current = true;
  const changed = prepareAppClaim({ barrier, scope, current: () => current, report: () => new Promise(resolve => { release = resolve; }) });
  current = false; release({ revision: 2 });
  assert.equal(await changed, false);
  assert.equal(await prepareAppClaim({ barrier, scope, current: () => true, report: async () => null }), false);
  assert.equal(await prepareAppClaim({ barrier, scope, current: () => true, report: async () => { throw Error('offline'); } }), false);
});
