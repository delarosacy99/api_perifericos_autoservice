// ES module. Bundle the licensed JSPrintManager library separately; no vendor SDK is used.
export class AutoserviceBridge {
  constructor({ baseUrl = 'http://127.0.0.1:8765', onEvent = () => {}, onState = () => {}, onError = () => {} } = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.onEvent = onEvent; this.onState = onState; this.onError = onError;
    this.token = null; this.cursor = null; this.stopped = true; this.retry = 500;
  }

  async request(path, body, method = 'POST', extraHeaders = {}) {
    const response = await fetch(`${this.baseUrl}/api/v1/bridge/${path}`, {
      method, cache: 'no-store', credentials: 'omit',
      headers: { 'Content-Type': 'application/json', ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}), ...extraHeaders },
      ...(body === undefined ? {} : { body: JSON.stringify(body) })
    });
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.error?.message || data.detail || `HTTP ${response.status}`); error.status = response.status; error.code = data.error?.code; throw error; }
    return data;
  }

  async connect(localApiKey, clientId = crypto.randomUUID()) {
    // In Tauri, perform this call in Rust and return only the ephemeral screen token.
    const session = await this.request('sessions/', { client_id: clientId }, 'POST', { 'X-Local-API-Key': localApiKey });
    this.token = session.screen_token; this.cursor = session.event_cursor;
    this.stopped = false; this.heartbeatMs = session.heartbeat_seconds * 1000;
    this.startHeartbeat(); this.openWebSocket();
    const config = await this.request('bootstrap/', {});
    const local = await this.request('state/', undefined, 'GET');
    if (config.recovery_required || local.checkout) {
      const recovered = await this.request('recover/', {});
      this.onState(recovered);
    } else this.onState(local);
    return config;
  }

  startHeartbeat() {
    const tick = async () => {
      if (this.stopped) return;
      try { await this.request('heartbeat/', {}); }
      catch (error) { this.onError(error); if (error.status === 401) { this.close(); return; } }
      if (!this.stopped) this.heartbeatTimer = setTimeout(tick, this.heartbeatMs);
    };
    this.heartbeatTimer = setTimeout(tick, this.heartbeatMs);
  }

  openWebSocket() {
    if (this.stopped) return;
    const wsUrl = new URL('/ws/v1/bridge/', this.baseUrl); wsUrl.protocol = wsUrl.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = this.ws = new WebSocket(wsUrl);
    ws.onopen = () => { this.retry = 500; ws.send(JSON.stringify({ type: 'authenticate', token: this.token, cursor: this.cursor })); };
    ws.onmessage = ({ data }) => {
      const message = JSON.parse(data);
      if (message.type === 'snapshot') this.onState(message.data);
      if (message.type === 'event') this.onEvent(message); // Refresh checkout after bridge.cash.synced; never add a balance locally.
      if (message.cursor !== undefined) this.cursor = message.cursor;
    };
    ws.onclose = ({ code }) => {
      if (this.stopped) return;
      if (code === 4401 || code === 4403) { this.onError(new Error(`WebSocket ${code}: renovar sesión/origen`)); this.close(); return; }
      this.reconnectTimer = setTimeout(() => this.openWebSocket(), this.retry + Math.random() * 250);
      this.retry = Math.min(10000, this.retry * 2);
    };
  }

  close() {
    this.stopped = true; clearTimeout(this.heartbeatTimer); clearTimeout(this.reconnectTimer);
    this.ws?.close(); this.token = null;
  }

  begin(requestKey) { return this.request('checkouts/', { request_key: requestKey }); }
  status(id) { return this.request(`checkouts/${id}/`, undefined, 'GET'); }
  startCash(id) { return this.request(`checkouts/${id}/cash/start/`, {}); }
  seal(id) { return this.request(`checkouts/${id}/seal/`, {}); }
  finalize(id, payload) { return this.request(`checkouts/${id}/finalize/`, payload); }
  previewTicket(id, code) { return this.request(`checkouts/${id}/tickets/preview/`, { code }); }
  selectTicket(id, code, total) { return this.request(`checkouts/${id}/tickets/select/`, { code, expected_total: total }); }
  removeTicket(id, code) { return this.request(`checkouts/${id}/tickets/remove/`, { code }); }
}

export function spoolerConfirmed(job, evidence) {
  const state = Number.isInteger(evidence?.state) ? evidence.state : 0;
  const flags = new Set(String(evidence?.['state-description'] || '').split(',').map(x => x.trim().toUpperCase()));
  const errors = ['ERROR', 'OFFLINE', 'PAPEROUT', 'DELETING', 'DELETED', 'BLOCKED_DEVQ', 'USER_INTERVENTION', 'PAUSED'];
  if (errors.some(x => flags.has(x)) || (state & (0x1 | 0x2 | 0x4 | 0x20 | 0x40 | 0x100 | 0x200 | 0x400))) return false;
  return Boolean((flags.has('PRINTED') || flags.has('COMPLETE') || (state & (0x80 | 0x1000))) && evidence?.name === job.document_name && evidence?.printer === job.printer_name);
}

export async function printWithJSPrintManager(bridge, JSPM, listedJob, renderDocument, timeoutMs = 90000) {
  if (JSPM.JSPrintManager.websocket_status !== JSPM.WSStatus.Open) throw new Error('JSPrintManager no está conectado');
  // renderDocument consumes the central print_payload/balance. Reuse the existing POS renderer.
  // Render BEFORE claim so a renderer error never creates an uncertain physical dispatch.
  const rendered = await renderDocument(listedJob.document);
  if (!rendered || !['pdf', 'raw'].includes(rendered.format)) throw new Error('El renderer debe devolver pdf/base64 o raw/commands');
  if (rendered.format === 'pdf' && (typeof rendered.base64 !== 'string' || !rendered.base64)) throw new Error('PDF base64 vacío');
  if (rendered.format === 'raw' && (typeof rendered.commands !== 'string' || !rendered.commands)) throw new Error('Comandos de impresión vacíos');
  const job = await bridge.request(`print-jobs/${listedJob.job_id}/claim/`, {});
  const cpj = new JSPM.ClientPrintJob();
  cpj.clientPrinter = new JSPM.InstalledPrinter(job.printer_name);
  if (rendered.format === 'pdf') {
    const file = new JSPM.PrintFilePDF(rendered.base64, JSPM.FileSourceType.Base64, `${job.document_name}.pdf`, 1);
    file.docName = job.document_name;
    cpj.files.push(file);
  } else {
    cpj.printerCommands = rendered.commands;
    cpj.printerCommandsDocName = job.document_name;
    cpj.printerCommandsCopies = 1;
  }
  let settled = false;
  return new Promise((resolve, reject) => {
    let timer;
    const report = async (outcome, evidence) => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      try {
        const result = await bridge.request(`print-jobs/${job.job_id}/report/`, { attempt_id: job.attempt_id, outcome, evidence });
        resolve({ outcome, result });
      } catch (error) {
        // Do not resend the print. Query status; the agent may have saved confirmation already.
        reject(error);
      }
    };
    cpj.onUpdated = evidence => { if (spoolerConfirmed(job, evidence)) void report('completed', evidence); };
    cpj.onFinished = evidence => { if (spoolerConfirmed(job, evidence)) void report('completed', evidence); };
    cpj.onError = data => { void report('uncertain', { source: 'jspm_error', message: String(data).slice(0, 500) }); };
    timer = setTimeout(() => { void report('uncertain', { source: 'jspm_timeout' }); }, timeoutMs);
    try {
      Promise.resolve(cpj.sendToClient()).catch(error => { void report('uncertain', { source: 'jspm_send_error', message: String(error).slice(0, 500) }); });
    } catch (error) { void report('uncertain', { source: 'jspm_send_error', message: String(error).slice(0, 500) }); }
    // sendToClient resolving only proves dispatch. A print status event must confirm completion.
  });
}
