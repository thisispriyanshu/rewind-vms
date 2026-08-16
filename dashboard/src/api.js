async function request(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

export const api = {
  meta: () => request("/api/meta"),
  listRuns: () => request("/api/runs"),
  tree: (runId) => request(`/api/runs/${runId}/tree`),
  state: (checkpointId) => request(`/api/checkpoints/${checkpointId}/state`),
  delta: (checkpointId) => request(`/api/checkpoints/${checkpointId}/delta`),
  files: (checkpointId) => request(`/api/checkpoints/${checkpointId}/files`),
  memories: (checkpointId) => request(`/api/checkpoints/${checkpointId}/memories`),
  diff: (a, b) => request(`/api/diff?a=${a}&b=${b}`),
  pendingEffects: (branchId) => request(`/api/branches/${branchId}/pending-effects`),
  rewind: (branchId, toCheckpointId) =>
    request("/api/rewind", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        branch_id: branchId,
        to_checkpoint_id: toCheckpointId,
        new_branch_name: "recovery",
      }),
    }),
  startDemo: (interval = 2.0) =>
    request("/api/demo/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ interval }),
    }),
};
