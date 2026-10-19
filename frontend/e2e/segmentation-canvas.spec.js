import { expect, test } from '@playwright/test';

const project = {
  id: 1,
  name: 'Segmentation project',
  description: '',
  task_type: 'segmentation',
  class_list: ['cat', 'dog'],
  source_volume: '/Volumes/demo/images',
  source_type: 'volume',
  serving_endpoint: null,
  endpoint_config: null,
  consensus_enabled: false,
  created_by: 'owner@example.com',
  created_at: '2026-10-16T00:00:00Z',
  sample_count: 1,
  labeled_count: 1,
  version: 1,
  parent_project_id: null,
};

const sample = {
  id: 7,
  project_id: 1,
  filepath: '/Volumes/demo/images/cat.svg',
  filename: 'cat.svg',
  status: 'labeled',
  locked_by: null,
  locked_at: null,
  width: 16,
  height: 12,
  annotations: [{
    id: 10,
    label: 'cat',
    ann_type: 'mask',
    mask_json: { size: [12, 16], counts: [0, 192] },
    is_draft: false,
  }],
  labels: ['cat'],
};

async function mockApi(page, selectedSample = sample) {
  await page.route('http://127.0.0.1:3000/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/config') {
      return route.fulfill({ json: { db_backend: 'lakebase' } });
    }
    if (path === '/api/projects/1') {
      return route.fulfill({ json: project });
    }
    if (path === '/api/projects/1/stats') {
      return route.fulfill({
        json: {
          total: 1, labeled: 1, unlabeled: 0, skipped: 0,
          pre_labeled: 0, embedded: 0, per_user: [],
        },
      });
    }
    if (path === '/api/projects/1/endpoint-status') {
      return route.fulfill({ json: { status: 'not_configured' } });
    }
    if (path === '/api/projects/1/samples') {
      return route.fulfill({ json: { items: [selectedSample], total: 1, page: 0, page_size: 10000 } });
    }
    if (path === '/api/projects/1/samples/7') {
      return route.fulfill({ json: selectedSample });
    }
    if (path === '/api/projects/1/samples/7/image') {
      return route.fulfill({
        contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="12"><rect width="16" height="12" fill="#888"/></svg>',
      });
    }
    return route.fulfill({
      status: 500,
      json: { detail: `Unmocked UI request: ${route.request().method()} ${path}` },
    });
  });
}

test('renders the active mask overlay on the initially selected image', async ({ page }) => {
  await mockApi(page);
  await page.goto('/projects/1/label?sample=7');
  await expect(page.getByText('cat.svg', { exact: true })).toBeVisible();
  await expect(page.getByText('Loading image...')).toHaveCount(0);

  const alpha = await page.getByTestId('mask-active-canvas').evaluate((canvas) => {
    const context = canvas.getContext('2d');
    return context.getImageData(
      Math.floor(canvas.width / 2),
      Math.floor(canvas.height / 2),
      1,
      1,
    ).data[3];
  });

  expect(alpha).toBeGreaterThan(0);
});

test('the first brush press creates a layer and paints it', async ({ page }) => {
  const blankSample = {
    ...sample,
    status: 'unlabeled',
    annotations: [],
    labels: [],
  };
  await mockApi(page, blankSample);
  await page.goto('/projects/1/label?sample=7');
  await expect(page.getByText('Click the image or + Layer to start painting')).toBeVisible();

  const activeCanvas = page.getByTestId('mask-active-canvas');
  const box = await activeCanvas.boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect(page.getByText('Mask layers (1)')).toBeVisible();

  const alpha = await activeCanvas.evaluate((canvas) => {
    const context = canvas.getContext('2d');
    return context.getImageData(
      Math.floor(canvas.width / 2),
      Math.floor(canvas.height / 2),
      1,
      1,
    ).data[3];
  });
  expect(alpha).toBeGreaterThan(0);
});

async function sampleActiveOverlay(page) {
  return page.getByTestId('mask-active-canvas').evaluate((canvas) => {
    const pixel = canvas.getContext('2d').getImageData(
      Math.floor(canvas.width / 2),
      Math.floor(canvas.height / 2),
      1,
      1,
    ).data;
    return { r: pixel[0], g: pixel[1], b: pixel[2], a: pixel[3] };
  });
}

test('changing class recolours the active brush overlay', async ({ page }) => {
  await mockApi(page);
  await page.goto('/projects/1/label?sample=7');
  await expect(page.getByText('cat.svg', { exact: true })).toBeVisible();
  await expect(page.getByText('Loading image...')).toHaveCount(0);

  const before = await sampleActiveOverlay(page);
  expect(before.a).toBeGreaterThan(0);
  // cat is CLASS_COLORS[0] = #4299e0 (blue-dominant)
  expect(before.b).toBeGreaterThan(before.r);

  await page.getByRole('button', { name: /dog/ }).click();
  await expect(page.getByText('Active: dog')).toBeVisible();

  const after = await sampleActiveOverlay(page);
  expect(after.a).toBeGreaterThan(0);
  // dog is CLASS_COLORS[1] = #e05252 (red-dominant)
  expect(after.r).toBeGreaterThan(after.b);
});
