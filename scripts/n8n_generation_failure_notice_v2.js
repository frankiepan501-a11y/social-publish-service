function value(input, key, fallback) {
  const raw = input && Object.prototype.hasOwnProperty.call(input, key) ? input[key] : undefined;
  if (raw === undefined || raw === null || raw === '') return fallback;
  return raw;
}

function shortError(input) {
  const raw = String(value(input, 'http_error', value(input, 'error', '')));
  if (!raw) return '未取得具体错误。';
  if (/EPROTO|SSL|TLS|handshake|alert/i.test(raw)) {
    return 'n8n 调用内容生成服务时发生 HTTPS/TLS 连接异常，通常是服务域名、网络或证书握手层问题，不是运营填错内容。';
  }
  if (/timeout|ETIMEDOUT|timed out|canceled/i.test(raw)) {
    return 'n8n 调用内容生成服务超时，通常是服务响应慢或网络不稳定。';
  }
  if (/401|unauthor/i.test(raw)) {
    return 'n8n 调用内容生成服务鉴权失败，通常是服务 token 或 credential 失效。';
  }
  if (/5\d\d|ECONNRESET|ENOTFOUND|ECONNREFUSED/i.test(raw)) {
    return '内容生成服务或网络返回系统级错误，需要 AI/开发检查服务健康。';
  }
  return raw.slice(0, 180);
}

function failedRecordLine(sample) {
  if (!sample || typeof sample !== 'object') return '没有定位到单条内容记录；这次更像是扫描入口或网络层失败。';
  const rid = sample.record_id || sample.content_record_id || sample.id || '';
  const title = sample.title || sample.topic || sample.product || sample.reason || '';
  if (!rid && !title) return '没有定位到单条内容记录；这次更像是扫描入口或网络层失败。';
  return [rid ? `记录：${rid}` : '', title ? `线索：${title}` : ''].filter(Boolean).join('；');
}

function buildGenerationFailureNotice(input) {
  const scanRunId = value(input, 'scan_run_id', '未生成');
  const status = value(input, 'status', '未知');
  const selected = value(input, 'selected', '未知');
  const generated = value(input, 'generated', '未知');
  const failed = value(input, 'failed', '未知');
  const httpStatus = value(input, 'http_status', '未取得');
  const replay = value(
    input,
    'replay_command',
    'POST /generate/scan {"write_back":false,"source":"replay","force":false,"limit":3}'
  );
  const sample = input && typeof input.failed_sample === 'object' ? input.failed_sample : null;
  const body = [
    '这是一张系统失败告警，运营不用手动修改内容。',
    '',
    '运营需要知道',
    '- 本轮没有生成新的 Caption、Hashtag 或图片 Prompt。',
    '- 已经在待审核的内容不会被删除。',
    '- Meta 发布闸没有打开，不会自动发到 FB/IG。',
    '- 这更像系统读取或网络调用失败，不代表选题、图片或运营填写有问题。',
    '',
    '影响范围',
    `- 扫描批次：${scanRunId}`,
    `- 选中 / 成功 / 失败：${selected} / ${generated} / ${failed}`,
    `- 涉及记录：${failedRecordLine(sample)}`,
    '',
    '系统判断',
    `- 失败环节：内容生成扫描 /generate/scan`,
    `- 接口状态：${httpStatus}`,
    `- 简明原因：${shortError(input)}`,
    '',
    '给 AI/开发的下一步',
    '1. 先只读重跑 replay，确认是否是偶发网络问题。',
    '2. 如果连续失败，检查 n8n 域名、social-publish-service /health、以及 n8n 的服务 token/credential。',
    '3. 如果 replay 成功，再让调度下一轮自动跑；不要直接打开发布闸。',
    '',
    `回放命令：${replay}`,
  ].join('\n');
  return {
    mode: 'auto',
    biz: 'SEO',
    level: 'P1',
    title: 'FB/IG 自动生成草稿失败',
    suffix: String(scanRunId) === 'no-scan-run-id' ? '扫描入口' : String(scanRunId),
    body,
    original: input,
  };
}

function normalizeGenerationScan(raw) {
  const scan = raw && typeof raw === 'object' ? raw : {};
  const results = Array.isArray(scan.results) ? scan.results : [];
  const failedItems = results.filter((item) => item && item.ok === false);
  const error = scan.error && typeof scan.error === 'object' ? scan.error.message : scan.error;
  return {
    ...scan,
    scan_run_id: value(scan, 'scan_run_id', 'no-scan-run-id'),
    http_status: scan.statusCode || scan.status_code || scan.httpCode || scan.http_status || '',
    http_error: error || scan.message || scan.cause || scan.http_error || '',
    failed_sample: failedItems[0] || scan.failed_sample || null,
    replay_command:
      scan.replay_command || 'POST /generate/scan {"write_back":false,"source":"replay","force":false,"limit":3}',
  };
}

if (typeof module !== 'undefined') {
  module.exports = { buildGenerationFailureNotice, normalizeGenerationScan };
}

if (typeof $input !== 'undefined') {
  const data = $input.first().json || {};
  return [{ json: buildGenerationFailureNotice(normalizeGenerationScan(data)) }];
}
