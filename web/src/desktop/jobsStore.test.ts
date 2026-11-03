// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert";
import { _resetDatasetJobs, datasetJobs, isFinished, jobsSummary, publishDatasetJobs } from "./jobsStore.ts";

const base = {
  key: "p|/d/v2xset/train",
  profileId: "p",
  label: "V2XSET · train",
  jobIds: ["700001"],
  ready: 15,
  preparing: 3,
  queued: 15,
  total: 33,
  viewerOpen: true,
  dataset: null,
};

test("the jobs indicator lists datasets in flight and says when one finished", () => {
  _resetDatasetJobs();
  assert.strictEqual(publishDatasetJobs(base), false);
  assert.strictEqual(jobsSummary(datasetJobs()[0]!), "15 ready · 3 preparing · 15 queued");
  assert.strictEqual(publishDatasetJobs({ ...base, ready: 30, preparing: 3, queued: 0 }), false);
  const done = publishDatasetJobs({ ...base, jobIds: [], ready: 33, preparing: 0, queued: 0 });
  assert.strictEqual(done, true);
  assert.deepStrictEqual(datasetJobs(), []);
  // A dataset that was never in flight is not "finished" news.
  assert.strictEqual(publishDatasetJobs({ ...base, jobIds: [], ready: 33, preparing: 0, queued: 0 }), false);
  assert.ok(isFinished({ jobIds: [], preparing: 0, queued: 0 }));
  assert.ok(!isFinished({ jobIds: ["1"], preparing: 0, queued: 0 }));
});
