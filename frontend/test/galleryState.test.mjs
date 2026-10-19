import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildGalleryParams,
  cancelDebounced,
  clampGalleryPage,
  galleryTotalPages,
  shouldShowGalleryPagination,
} from '../src/utils/galleryState.js';

test('galleryTotalPages handles empty and partial pages', () => {
  assert.equal(galleryTotalPages(0, 24), 0);
  assert.equal(galleryTotalPages(24, 24), 1);
  assert.equal(galleryTotalPages(25, 24), 2);
});

test('clampGalleryPage keeps the page in the available range', () => {
  assert.equal(clampGalleryPage(-1, 100, 24), 0);
  assert.equal(clampGalleryPage(2, 100, 24), 2);
  assert.equal(clampGalleryPage(8, 25, 24), 1);
  assert.equal(clampGalleryPage(3, 0, 24), 0);
});

test('pagination is shown only for a multi-page normal gallery', () => {
  const normal = {
    similarMode: false,
    duplicateMode: false,
    outlierMode: false,
    viewMode: 'gallery',
    totalPages: 2,
  };
  assert.equal(shouldShowGalleryPagination(normal), true);
  assert.equal(shouldShowGalleryPagination({ ...normal, similarMode: true }), false);
  assert.equal(shouldShowGalleryPagination({ ...normal, duplicateMode: true }), false);
  assert.equal(shouldShowGalleryPagination({ ...normal, outlierMode: true }), false);
  assert.equal(shouldShowGalleryPagination({ ...normal, viewMode: 'clusters' }), false);
  assert.equal(shouldShowGalleryPagination({ ...normal, totalPages: 1 }), false);
});

test('buildGalleryParams omits empty filters', () => {
  assert.deepEqual(
    buildGalleryParams({
      page: 2,
      pageSize: 24,
      status: 'labeled',
      label: '',
      labeler: 'user@example.com',
      filename: undefined,
    }),
    {
      page: 2,
      page_size: 24,
      status: 'labeled',
      labeler: 'user@example.com',
    },
  );
});

test('buildGalleryParams includes consensus label scope and state', () => {
  assert.deepEqual(
    buildGalleryParams({
      page: 0,
      pageSize: 24,
      label: 'cat',
      labelScope: 'mine',
      consensusState: 'review',
    }),
    {
      page: 0,
      page_size: 24,
      label: 'cat',
      label_scope: 'mine',
      consensus_state: 'review',
    },
  );
});

test('cancelDebounced clears and resets an active timer', () => {
  const ref = { current: setTimeout(() => {}, 10_000) };
  cancelDebounced(ref);
  assert.equal(ref.current, null);
});
