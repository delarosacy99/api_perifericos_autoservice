import test from 'node:test';
import assert from 'node:assert/strict';
import { spoolerConfirmed, printWithJSPrintManager } from './autoservice-bridge.mjs';

const listedJob = { job_id: 'job-1', document_name: 'DEMP-job-1', printer_name: 'CUSTOM K80', document: { kind: 'ticket', payload: {} } };
const success = { state: 128, 'state-description': 'PRINTED', name: 'DEMP-job-1', printer: 'CUSTOM K80' };

test('requires a matching successful spooler job', () => {
  assert.equal(spoolerConfirmed(listedJob, success), true);
  assert.equal(spoolerConfirmed(listedJob, {}), false);
  assert.equal(spoolerConfirmed(listedJob, { ...success, printer: 'other' }), false);
  assert.equal(spoolerConfirmed(listedJob, { ...success, state: 130 }), false);
  assert.equal(spoolerConfirmed(listedJob, { ...success, 'state-description': 'PRINTED,ERROR' }), false);
});

function fake({ finish = false, updated = false, sendError = false } = {}) {
  const calls = []; let sends = 0;
  const bridge = { async request(path, data) { calls.push({ path, data }); return path.endsWith('/claim/') ? { ...listedJob, attempt_id: 'attempt-1' } : { central: { can_reset: true } }; } };
  const JSPM = {
    WSStatus: { Open: 1 }, JSPrintManager: { websocket_status: 1 },
    InstalledPrinter: class { constructor(name) { this.name = name; } },
    ClientPrintJob: class {
      constructor() { this.files = []; }
      async sendToClient() { sends++; if (sendError) throw new Error('lost connection'); if (finish) this.onFinished({}); if (updated) this.onUpdated(success); }
    }
  };
  return { bridge, JSPM, calls, sends: () => sends };
}
const render = async () => ({ format: 'raw', commands: 'test' });

test('resolved send and empty finished callback do not confirm printing', async () => {
  const f = fake({ finish: true });
  const result = await printWithJSPrintManager(f.bridge, f.JSPM, listedJob, render, 10);
  assert.equal(result.outcome, 'uncertain'); assert.equal(f.sends(), 1);
});

test('successful callback reports one attempt without duplicate printing', async () => {
  const f = fake({ updated: true });
  const result = await printWithJSPrintManager(f.bridge, f.JSPM, listedJob, render, 10);
  assert.equal(result.outcome, 'completed'); assert.equal(f.sends(), 1);
  assert.equal(f.calls.at(-1).data.attempt_id, 'attempt-1');
});

test('dispatch error stays uncertain', async () => {
  const f = fake({ sendError: true });
  assert.equal((await printWithJSPrintManager(f.bridge, f.JSPM, listedJob, render, 10)).outcome, 'uncertain');
});

test('renderer failure happens before claiming or sending', async () => {
  const f = fake();
  await assert.rejects(printWithJSPrintManager(f.bridge, f.JSPM, listedJob, async () => { throw new Error('renderer'); }, 10));
  assert.equal(f.calls.length, 0); assert.equal(f.sends(), 0);
});
