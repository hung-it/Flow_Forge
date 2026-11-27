import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { Trend } from 'k6/metrics';

const runs = Number(__ENV.BENCHMARK_RUNS);
const submittedAt = new Trend('benchmark_submitted_at_ms');
const endpoint = 'http://api:8000/api/v1/workflows/' + __ENV.BENCHMARK_WORKFLOW + '/runs';

export const options = {
  scenarios: {
    submit: {
      executor: 'shared-iterations',
      vus: Math.min(5, runs),
      iterations: runs,
      maxDuration: '45s',
      gracefulStop: '0s',
    },
  },
  summaryTrendStats: ['min', 'med', 'p(95)', 'p(99)', 'max'],
  thresholds: { checks: ['rate==1'], http_req_failed: ['rate==0'] },
};

export default function () {
  const key = 'bench-' + exec.scenario.iterationInTest;
  submittedAt.add(Date.now());
  const response = http.post(endpoint, JSON.stringify({
    version: 1,
    input: { document: { title: 'FlowForge benchmark', content: 'Synthetic document.' } },
  }), {
    headers: {
      Authorization: 'Bearer ' + __ENV.BENCHMARK_TOKEN,
      'Content-Type': 'application/json',
      'Idempotency-Key': key,
    },
    tags: { name: 'POST /api/v1/workflows/{workflow_id}/runs' },
    timeout: '10s',
  });
  check(response, { 'run accepted': value => value.status === 202 });
}

export function handleSummary(data) {
  return { stdout: JSON.stringify(data) + '\n' };
}
